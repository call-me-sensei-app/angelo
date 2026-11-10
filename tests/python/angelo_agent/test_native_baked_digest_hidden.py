# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The baked-material digest reads what a render shows, whatever the viewport hides, and every UDIM tile.

``export_baked_reads.Reader`` runs here against stand-in objects, meshes and images, with a stand-in
``_bpy._angelo_digest`` that answers as the native reader does (its contract: ``bpy_angelo_digest.cc``). What real
Blender changes for each case was measured in stock Blender 5.2 (doc/angelo/long_jobs.rst, "Baked materials at
export"); ``test_blender_baked_export_hidden.py`` repeats these cases in the built app with the native reader.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_baked_digest_frames import fresh_export_modules  # noqa: E402

IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def moved(x):
    matrix = [list(row) for row in IDENTITY]
    matrix[0][3] = x
    return matrix


class ID:
    _next = [1]

    def __init__(self, name):
        self.name, self.session_uid, self.is_evaluated = name, ID._next[0], True
        ID._next[0] += 1


class Collection(list):
    def foreach_get(self, name, values):
        values[:] = numpy.asarray([getattr(item, name) for item in self], dtype=values.dtype).reshape(-1)


class Attributes(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)


class Mesh(ID):
    """A box of eight points, enough of a mesh for the digest's reads."""

    def __init__(self, name, offset=0.0):
        super().__init__(name)
        self.move(offset)
        self.edges, self.uv_layers = [], SimpleNamespace(active=None)
        self.texspace_location, self.texspace_size = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)

    def move(self, offset):
        points = [(x + offset, y, z) for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)]
        self.vertices = Collection(SimpleNamespace(co=point) for point in points)
        self.attributes = Attributes([SimpleNamespace(
            name="position", domain="POINT", data_type="FLOAT_VECTOR",
            data=Collection(SimpleNamespace(vector=point) for point in points))])
        self.loops = Collection(SimpleNamespace(vertex_index=index) for index in range(8))
        self.polygons = Collection(SimpleNamespace(loop_start=start) for start in (0, 4))
        self.corner_normals = Collection(SimpleNamespace(vector=(0.0, 0.0, 1.0)) for _ in range(8))


class Object(ID):
    def __init__(self, name, mesh, x=0.0):
        super().__init__(name)
        self.type, self.data, self.matrix_world = "MESH", mesh, moved(x)
        self.modifiers, self.material_slots, self.particle_systems = [], [], []
        self.hide_render = self.hide_viewport = self.hidden = self.is_instancer = False
        self.show_instancer_for_render = self.show_instancer_for_viewport = True
        self.parent, self.instance_type, self.color, self.pass_index = None, "NONE", (1, 1, 1, 1), 0
        self.made = []   # (geometry, original, matrix): the instances it makes, as evaluated.
        self.twin = None   # Its evaluated object, when the viewport's depsgraph doesn't hold it.
        self._original = self

    @property
    def original(self):
        return self._original

    @property
    def bound_box(self):
        points = numpy.asarray([item.co for item in self.data.vertices])
        low, high = points.min(axis=0), points.max(axis=0)
        return [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]

    def evaluated_get(self, depsgraph):
        return self


class Pixels(list):
    def foreach_get(self, values):   # Image.pixels: the first tile's, as floats.
        values[:] = numpy.asarray(self, dtype=values.dtype)


class Tile(SimpleNamespace):
    pass


class Image(ID):
    def __init__(self, tiles):
        super().__init__("Paint")
        self.source, self.filepath_raw, self.library, self.packed_file, self.packed_files = "TILED", "", None, None, []
        self.tiles = [Tile(number=number, is_generated_tile=True, generated_type="BLANK",
                           generated_color=(0.2, 0.2, 0.2, 1.0), generated_width=64, generated_height=64,
                           use_generated_float=False) for number in tiles]
        self.is_dirty, self.has_data = False, True
        self.pixels = Pixels([0.0] * 16)
        self.unsaved = {}   # cache index -> pixels digest of an edited buffer (the native reader's view).


class Native:
    """``_bpy._angelo_digest`` as bpy_angelo_digest.cc answers."""

    @staticmethod
    def pixels(image, user, scene_frame, tiles):
        """Each tile's buffer as a bake copies it: its unsaved pixels, else what it loads from (``test_native_baked_
        digest_buffers`` models what Blender holds against its sources in full)."""
        found = []
        for tile in tiles:
            pixels = image.unsaved.get(tile)
            if pixels is None:
                packed = [each.packed_file.data for each in image.packed_files if each.tile_number == tile]
                entry = next(each for each in image.tiles if each.number == tile)
                pixels = repr(packed[0] if packed else (entry.generated_type, entry.generated_color)).encode()
            found.append((tile, 64, 64, 4, False, pixels))
        return found

    @staticmethod
    def instances(depsgraph, objects, callback):
        for obj in objects:
            for geometry, original, matrix in obj.made:
                callback(geometry, original, matrix, True)
            instancer = bool(obj.made) or obj.is_instancer
            if not instancer or obj.show_instancer_for_render:
                callback(obj.twin or obj, obj, obj.matrix_world, False)


class Depsgraph:
    def __init__(self, objects):
        self.objects = objects
        collection = SimpleNamespace(objects=objects, hide_render=False, hide_viewport=False)
        layer = SimpleNamespace(exclude=False, collection=collection, children=[])
        self.view_layer = SimpleNamespace(layer_collection=layer, material_override=None)
        self.scene = SimpleNamespace(objects=objects, world=None, camera=None, frame_current=1,
                                     render=SimpleNamespace(use_multiview=False))

    @property
    def object_instances(self):
        """The viewport's list: nothing of an object hidden with the eye or disabled in viewports."""
        for obj in self.objects:
            if obj.hidden or obj.hide_viewport:
                continue
            for geometry, original, matrix in obj.made:
                yield SimpleNamespace(object=geometry, parent=obj, is_instance=True, matrix_world=matrix)
            if not obj.made or obj.show_instancer_for_viewport:
                yield SimpleNamespace(object=obj, parent=None, is_instance=False, matrix_world=obj.matrix_world)


def stand_in_bpy():
    bpy = ModuleType("bpy")
    bpy.types = SimpleNamespace(ID=ID, Object=Object, Mesh=Mesh, Image=Image, NodeTree=type("NodeTree", (ID,), {}),
                                Text=type("Text", (ID,), {}))
    bpy.path = SimpleNamespace(abspath=lambda path, library=None: path)
    bpy.data = SimpleNamespace(objects={})
    return bpy


class BakedDigestHiddenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native = ModuleType("_bpy")
        native._angelo_digest = Native()
        with patch.dict(sys.modules, {"bpy": stand_in_bpy(), "_bpy": native}):
            cls.addClassCleanup(fresh_export_modules())   # Else an earlier import's reader may stand in.
            cls.reads = importlib.import_module("angelo_agent.export_baked_reads")
            cls.images = sys.modules.get("angelo_agent.export_baked_images")   # Loaded by the reads.
            cls.bpy = sys.modules["bpy"]

    def setUp(self):
        self.modules = patch.dict(sys.modules, {"bpy": self.bpy})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def scene(self):
        target = Object("Target", Mesh("TargetMesh"))
        rock = Object("Rock", Mesh("RockMesh"), x=50.0)   # Far away: seen only through the scatter's instances.
        scatter = Object("Scatter", Mesh("ScatterMesh"), x=2.0)
        scatter.made = [(rock, rock, moved(2.0)), (rock, rock, moved(3.0))]
        return target, rock, scatter, Depsgraph([target, rock, scatter])

    def digest(self, depsgraph, target, sources=()):
        return self.reads.Reader(depsgraph).digest(target, list(sources), None, True)

    def test_hiding_an_instancer_with_the_eye_or_disabling_it_leaves_the_digest(self):
        target, _rock, scatter, depsgraph = self.scene()
        shown = self.digest(depsgraph, target)
        scatter.hidden = True
        self.assertEqual(self.digest(depsgraph, target), shown)
        scatter.hidden, scatter.hide_viewport = False, True
        self.assertEqual(self.digest(depsgraph, target), shown)

    def test_an_edit_to_what_a_hidden_instancer_places_makes_the_bake_stale(self):
        for hide in ("hidden", "hide_viewport"):
            with self.subTest(hide=hide):
                target, rock, scatter, depsgraph = self.scene()
                setattr(scatter, hide, True)
                before = self.digest(depsgraph, target)
                rock.data.move(0.25)   # The instanced rock's geometry: the render's AO sees its instances.
                self.assertNotEqual(self.digest(depsgraph, target), before)
                scatter.made[0] = (rock, rock, moved(2.5))   # One instance moved.
                after_move = self.digest(depsgraph, target)
                scatter.made[0] = (rock, rock, moved(2.0))
                self.assertNotEqual(after_move, self.digest(depsgraph, target))

    def test_render_visibility_still_counts(self):
        target, _rock, scatter, depsgraph = self.scene()
        shown = self.digest(depsgraph, target)
        scatter.hide_render = True
        self.assertNotEqual(self.digest(depsgraph, target), shown)
        scatter.hide_render = False
        scatter.show_instancer_for_viewport = False   # Viewport only: the render still shows the scatter's mesh.
        self.assertEqual(self.digest(depsgraph, target), shown)
        scatter.show_instancer_for_render = False
        self.assertNotEqual(self.digest(depsgraph, target), shown)

    def test_a_source_disabled_in_viewports_is_read_as_evaluated(self):
        target, _rock, _scatter, depsgraph = self.scene()
        posed = Mesh("HighPosed", offset=0.5)   # Its mesh as its modifiers leave it (an armature's pose, say).
        high = Object("High", posed)
        depsgraph.objects.append(high)
        shown = self.digest(depsgraph, target, [high])
        # Disabled in viewports: the viewport's depsgraph doesn't hold it, so its own data is its rest mesh, and
        # only an evaluation of its own (the native reader's temporary depsgraph) has the posed one.
        high.hide_viewport, high.is_evaluated, high.data = True, False, Mesh("HighRest")
        high.twin = Object("High", posed)
        high.twin._original = high
        self.assertEqual(self.digest(depsgraph, target, [high]), shown)
        posed.move(0.75)   # The pose changed while it was disabled.
        self.assertNotEqual(self.digest(depsgraph, target, [high]), shown)

    def test_unsaved_paint_on_any_tile_makes_the_bake_stale(self):
        target, _rock, _scatter, depsgraph = self.scene()
        image = Image([1001, 1002, 1003])
        image.is_dirty, image.unsaved = True, {1001: b"a" * 16}
        reader = self.reads.Reader(depsgraph)
        before = reader.image(image)
        image.unsaved[1002] = b"b" * 16   # Painting tile 1002 too: tile 1001's pixels are unchanged.
        self.assertNotEqual(self.reads.Reader(depsgraph).image(image), before)
        painted = self.reads.Reader(depsgraph).image(image)
        image.unsaved[1002] = b"c" * 16
        self.assertNotEqual(self.reads.Reader(depsgraph).image(image), painted)
        image.unsaved[1002] = b"b" * 16
        self.assertEqual(self.reads.Reader(depsgraph).image(image), painted)

    def test_every_tiles_source_counts(self):
        _target, _rock, _scatter, depsgraph = self.scene()
        image = Image([1001, 1002])
        before = self.reads.Reader(depsgraph).image(image)
        image.tiles[1].generated_color = (0.0, 1.0, 0.0, 1.0)   # Add Tile's fill, never saved: no file.
        self.assertNotEqual(self.reads.Reader(depsgraph).image(image), before)
        filled = self.reads.Reader(depsgraph).image(image)
        image.packed_files = [SimpleNamespace(tile_number=number, view=0, packed_file=SimpleNamespace(data=data))
                              for number, data in ((1001, b"one"), (1002, b"two"))]
        packed = self.reads.Reader(depsgraph).image(image)
        self.assertNotEqual(packed, filled)
        image.packed_files[1].packed_file.data = b"tw0"   # Tile 1002's packed file only.
        self.assertNotEqual(self.reads.Reader(depsgraph).image(image), packed)

    def test_a_tile_file_changed_on_disk_makes_the_bake_stale_in_either_tile_naming(self):
        # Blender names a tiled image's files with <UDIM> (paint.1002.png) or <UVTILE> (paint.u2_v1.png).
        _target, _rock, _scatter, depsgraph = self.scene()
        for token, names in (("<UDIM>", {1001: "1001", 1002: "1002", 1013: "1013"}),
                             ("<UVTILE>", {1001: "u1_v1", 1002: "u2_v1", 1013: "u3_v2"})):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as folder:
                image = Image(sorted(names))
                image.source, image.filepath_raw = "TILED", os.path.join(folder, "paint.%s.png" % token)
                files = {}
                for tile in image.tiles:
                    tile.is_generated_tile = False
                    files[tile.number] = os.path.join(folder, "paint.%s.png" % names[tile.number])
                    Path(files[tile.number]).write_bytes(b"tile")
                before = self.reads.Reader(depsgraph).image(image)
                self.assertEqual(self.reads.Reader(depsgraph).image(image), before)
                for number in (1002, 1013):   # A later tile, saved again with other pixels, then reloaded.
                    Path(files[number]).write_bytes(b"tile, repainted")
                    changed = self.reads.Reader(depsgraph).image(image)
                    self.assertNotEqual(changed, before, number)
                    before = changed
                os.remove(files[1013])   # A tile's file going missing is a change too.
                self.assertNotEqual(self.reads.Reader(depsgraph).image(image), before)

    def test_without_the_native_reader_other_tiles_read_as_unknown(self):
        _target, _rock, _scatter, depsgraph = self.scene()
        images = self.images
        self.assertIsNotNone(images)
        image = Image([1001, 1002])
        image.is_dirty = True
        with patch.object(images, "NATIVE", None):
            first = self.reads.Reader(depsgraph).image(image)
            self.assertNotEqual(self.reads.Reader(depsgraph).image(image), first)   # Unknown is stale.
            image.tiles = image.tiles[:1]
            single = self.reads.Reader(depsgraph).image(image)
            self.assertEqual(self.reads.Reader(depsgraph).image(image), single)


if __name__ == "__main__":
    unittest.main()
