# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a change reaches (``web_link_users``; doc/angelo/web_link.rst, "Live frames"), on stand-in objects.

Covered: each evaluated object is judged once from ``depsgraph.ids`` (a material a modifier sets, an object outside
the scene), and every object that shows one through instancing is found from the originals (a collection instance,
nested instances, geometry nodes naming the object or its collection in a socket or a modifier input, a particle
system, a vertex-instancing parent), never by walking ``depsgraph.object_instances``, at a cost that doesn't grow
with copies (break-it round 9, H1); an image's files are each stamped as Blender names them: every UDIM tile, a
sequence's frame, a movie (round 9, M2), and a same-size file replaced by a rename with its old modification time is
a change (round 9, L2). A file keeps one key, in one form, for as long as it is unchanged: by its stat when first
seen once the file system's clock has passed it (its bytes never read), by its bytes when first seen before that
(read again until the clock passes it, then once more), so neither the clock's tick nor its passing reads as a change
(round 13's CI and its follow-up).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import web_link_live as live, web_link_users as users  # noqa: E402


class Obj(SimpleNamespace):
    """An original object (``original`` is itself); ``evaluated`` makes its evaluated copy."""

    def __init__(self, uid, data=None, materials=(), slots=(), **fields):
        defaults = dict(instance_type="NONE", instance_collection=None, children=[], particle_systems=[],
                        modifiers=[], material_slots=[SimpleNamespace(material=material(m)) for m in slots])
        super().__init__(session_uid=uid, **dict(defaults, **fields))
        self.original = self
        self.data = None if data is None else SimpleNamespace(
            session_uid=data, materials=[material(m) for m in materials])
        self.data_original = self.data


class EvaluatedObject:   # Its type is ``bpy.types.Object`` in the stand-in ``bpy``.
    def __init__(self, obj):
        self.original = obj
        self.data = None if obj.data is None else SimpleNamespace(original=obj.data, materials=[
            SimpleNamespace(original=m) for m in obj.data.materials])
        self.material_slots = [SimpleNamespace(material=SimpleNamespace(original=slot.material))
                               for slot in obj.material_slots]


def material(uid, image=None):
    nodes = [SimpleNamespace(image=SimpleNamespace(session_uid=image))] if image else []
    return SimpleNamespace(session_uid=uid, node_tree=SimpleNamespace(session_uid=uid + 1, nodes=nodes))


def collection(uid, *objects):
    return SimpleNamespace(session_uid=uid, all_objects=list(objects))


class Unwalked:
    def __init__(self, *objects):
        self.ids = [EvaluatedObject(obj) for obj in objects] + [SimpleNamespace(session_uid=1)]   # A mesh, say.

    @property
    def object_instances(self):
        raise AssertionError("object_instances walked")


class Nodes:
    """A Blender 5.2 geometry-nodes modifier: its inputs in ``properties.inputs.<identifier>.value`` (generated
    property groups), never ID properties: ``keys()`` and ``[]`` raise as Blender's do (break-it round 10, H1)."""
    type = "NODES"

    def __init__(self, node_group=None, **inputs):
        self.node_group = node_group
        holder = SimpleNamespace(**{key: SimpleNamespace(value=value) for key, value in inputs.items()})
        holder.bl_rna = SimpleNamespace(properties=[SimpleNamespace(type="POINTER", identifier="rna_type")] + [
            SimpleNamespace(type="POINTER", identifier=key) for key in inputs])
        self.properties = SimpleNamespace(inputs=holder)

    def keys(self):
        raise TypeError("bpy_struct.keys(): this type doesn't support IDProperties")

    def __getitem__(self, key):
        raise TypeError("bpy_struct[key]: this type doesn't support IDProperties")


BPY = SimpleNamespace(types=SimpleNamespace(Object=EvaluatedObject),
                      path=SimpleNamespace(abspath=lambda path, start=None: path))


class EvaluatedUsersTests(unittest.TestCase):
    def setUp(self):
        live.reset()
        self.addCleanup(live.reset)
        patch = mock.patch.dict(sys.modules, {"bpy": BPY})
        patch.start()
        self.addCleanup(patch.stop)

    def changed(self, data, scene, evaluated=None):
        live._session["dirty_data"] = set(data)
        return live.changed_objects(scene, Unwalked(*(scene if evaluated is None else evaluated)))

    def test_a_material_set_by_a_modifier_reaches_its_object(self):
        """Geometry nodes' Set Material, a Boolean's transferred material: in the evaluated data, not the slots."""
        self.assertEqual(self.changed({900}, [Obj(1, data=101, materials=[900])]), {1})
        self.assertEqual(self.changed({900}, [Obj(1, data=101, materials=[901])]), set())

    def test_an_object_outside_the_scene_reaches_every_instancer_at_any_depth(self):
        part = Obj(10, data=300, materials=[700])
        kit = collection(20, part)
        inner = Obj(30, instance_type="COLLECTION", instance_collection=kit)   # Inside another instanced collection.
        shelf = collection(40, inner)
        outer = [Obj(50 + index, instance_type="COLLECTION", instance_collection=shelf) for index in range(2)]
        direct = Obj(60, instance_type="COLLECTION", instance_collection=kit)
        scene = outer + [direct, Obj(70)]
        for data in ({700}, {300}, {799}):
            with self.subTest(data=data):
                got = self.changed(data, scene, evaluated=scene + [part, inner])
                expected = {10, 30, 50, 51, 60} if data != {799} else set()
                self.assertEqual(got, expected)

    def test_geometry_nodes_particles_and_vertex_instancing_reach_their_instancer(self):
        pebble = Obj(10, data=300, materials=[700])
        crates = collection(20, Obj(11, data=301, materials=[700]))
        socket = SimpleNamespace(default_value=pebble)
        tree = SimpleNamespace(session_uid=90, nodes=[SimpleNamespace(inputs=[socket,
                                                                               SimpleNamespace(default_value=2)])])

        by_socket = Nodes(tree)
        by_input = Nodes(Socket_2=crates)
        scatter = Obj(50, modifiers=[by_socket])
        shelf = Obj(51, modifiers=[by_input])
        emitter = Obj(52, particle_systems=[SimpleNamespace(settings=SimpleNamespace(
            render_type="OBJECT", instance_object=pebble, instance_collection=None))])
        verts = Obj(53, instance_type="VERTS")
        child = Obj(12, data=302, materials=[700], parent=verts)
        verts.children = [child]
        unrelated = Obj(54, modifiers=[Nodes()])
        scene = [scatter, shelf, emitter, verts, child, unrelated]
        got = self.changed({700}, scene, evaluated=scene + [pebble, crates.all_objects[0]])
        self.assertEqual(got, {10, 11, 12, 50, 51, 52, 53})

    def test_an_object_linked_material_counts(self):
        self.assertEqual(self.changed({800}, [Obj(2, data=102, slots=[800])]), {2})

    def test_images_inside_the_material(self):
        obj = Obj(10, data=300)
        obj.data.materials = [material(700, image=901)]
        self.assertEqual(self.changed({901}, [obj]), {10})

    def test_without_a_change_to_data_nothing_is_read(self):
        class Unread:
            @property
            def ids(self):
                raise AssertionError("ids read")
        live._session["dirty"] = {4}
        live._session["dirty_data"] = set()
        self.assertEqual(live.changed_objects([], Unread()), {4})

    def test_the_cost_follows_objects_never_copies(self):
        """A scatter of any size is one object and one node group here: the depsgraph's instances are never read
        (``Unwalked``), so 10 or 1,000,000 copies are the same work (timed with Blender: test_blender_web_link_cost)."""
        for copies in (10, 1_000_000):
            with self.subTest(copies=copies):
                pebble = Obj(10, data=300, materials=[700])
                tree = SimpleNamespace(session_uid=90, nodes=[SimpleNamespace(inputs=[
                    SimpleNamespace(default_value=pebble)])])

                scene = [Obj(50, modifiers=[Nodes(tree)], copies=copies), Obj(60, data=301, materials=[702])]
                self.assertEqual(self.changed({702}, scene, evaluated=scene + [pebble]), {60})
                self.assertEqual(self.changed({700}, scene, evaluated=scene + [pebble]), {10, 50})


    def test_an_object_only_geometry_nodes_instance_is_judged_on_its_original(self):
        """A collection only geometry nodes instance is not in ``depsgraph.ids``: its objects are judged as they are."""
        crate = Obj(10, data=300, materials=[700])
        tree = SimpleNamespace(session_uid=90, nodes=[SimpleNamespace(inputs=[
            SimpleNamespace(default_value=collection(20, crate))])])
        shelf = Obj(50, modifiers=[Nodes(tree)])
        self.assertEqual(self.changed({700}, [shelf], evaluated=[shelf]), {10, 50})

    def test_many_instancers_of_one_collection_cost_one_read_each(self):
        """Round 10, M2: 10,000 instancers of one collection, its member changed: the collection is read once and
        each instancer once, never a set rebuilt per instancer."""
        reads = []

        class Kit:
            session_uid = 20

            @property
            def all_objects(self):
                reads.append(1)
                return [part]
        part = Obj(10, data=300, materials=[700])
        kit = Kit()
        scene = [Obj(1000 + index, instance_type="COLLECTION", instance_collection=kit) for index in range(10_000)]
        got = self.changed({700}, scene, evaluated=scene + [part])
        self.assertEqual(len(got), 10_001)
        self.assertLessEqual(len(reads), 2, "the collection's objects are read once to list and once to look up")


def unsettled():
    """The file system's clock hasn't passed any stat yet (a FAT, exFAT or network volume just after a write)."""
    clock = users.change_keys.change_clock
    return mock.patch.multiple(clock, settle=mock.Mock(side_effect=clock.Unsettled("tick")),
                               settled=mock.Mock(return_value=False))


class FileStampTests(unittest.TestCase):
    def setUp(self):
        users.KEYS.clear()
        self.addCleanup(users.KEYS.clear)
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-web-files-"))
        self.addCleanup(lambda: [path.unlink() for path in self.folder.iterdir()] and self.folder.rmdir())
        patch = mock.patch.dict(sys.modules, {"bpy": BPY})
        patch.start()
        self.addCleanup(patch.stop)

    def image(self, name, source, tiles=()):
        return SimpleNamespace(source=source, packed_file=None, packed_files=[], filepath_raw=str(self.folder / name),
                               library=None, tiles=[SimpleNamespace(number=n, is_generated_tile=False) for n in tiles])

    def write(self, name, data):
        (self.folder / name).write_bytes(data)

    def test_every_udim_tile_file_is_stamped(self):
        for tile in (1001, 1002):
            self.write("wood.%d.png" % tile, b"tile")
        image = self.image("wood.<UDIM>.png", "TILED", tiles=(1001, 1002))
        before = users.file_stamp(image)
        self.assertEqual(len(before), 2)
        self.write("wood.1002.png", b"TILE!")
        self.assertNotEqual(users.file_stamp(image), before, "a tile's file rewritten")

    def test_a_sequence_stamps_the_frame_it_reads(self):
        for frame in (1, 2):
            self.write("shot.%04d.png" % frame, b"frame")
        image = self.image("shot.0001.png", "SEQUENCE")
        before = users.file_stamp(image, frames={2})
        self.write("shot.0001.png", b"other")
        self.assertEqual(users.file_stamp(image, frames={2}), before, "another frame's file")
        self.write("shot.0002.png", b"FRAME")
        self.assertNotEqual(users.file_stamp(image, frames={2}), before)

    def test_a_same_size_replace_by_rename_with_the_old_time_is_a_change(self):
        self.write("chair.png", b"aaaa")
        image = self.image("chair.png", "FILE")
        target = self.folder / "chair.png"
        info = target.stat()
        before = users.file_stamp(image)
        self.write("next.png", b"bbbb")
        os.utime(self.folder / "next.png", ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(self.folder / "next.png", target)
        self.assertNotEqual(users.file_stamp(image), before)

    def test_a_file_the_clock_hasnt_passed_keeps_one_key_until_its_bytes_change(self):
        """Round 13's CI (Windows): a stat the file system's clock hasn't passed got a fresh key on every call, so an
        unchanged file read as changed at every poll. Unsettled, a file is keyed by its bytes."""
        self.write("shot.0001.png", b"frame one")
        image = self.image("shot.0001.png", "FILE")
        with unsettled():
            before = users.file_stamp(image)
            self.assertEqual(users.file_stamp(image), before, "unchanged: the same key")
            self.write("shot.0001.png", b"frame ONE")
            self.assertNotEqual(users.file_stamp(image), before, "its bytes changed")

    def test_the_clock_passing_an_unchanged_file_changes_nothing(self):
        """Round 13's follow-up: a digest while unsettled and a stat once settled made every saved file read as
        changed once more when the clock passed it (one extra live update after each save)."""
        self.write("wood.png", b"saved a moment ago")
        image = self.image("wood.png", "FILE")
        with unsettled():
            before = users.file_stamp(image)
        self.assertEqual(users.file_stamp(image), before, "the clock passed it; its bytes are the same")
        self.assertEqual(users.file_stamp(image), before, "and stay the same")
        self.write("wood.png", b"saved once more, later")
        self.assertNotEqual(users.file_stamp(image), before)

    def test_a_write_in_the_tick_that_no_stat_shows_is_seen_when_the_clock_passes(self):
        """A same-size write inside the clock's tick keeps every time: the bytes are read once more when settled."""
        self.write("wood.png", b"aaaa")
        image = self.image("wood.png", "FILE")
        info = os.stat(self.folder / "wood.png")
        with mock.patch.object(users.os, "stat", return_value=info):
            with unsettled():
                before = users.file_stamp(image)
            self.write("wood.png", b"bbbb")
            after = users.file_stamp(image)
            self.assertNotEqual(after, before)
            self.assertEqual(users.file_stamp(image), after)

    def test_a_file_first_seen_settled_is_never_read(self):
        """Off Windows every stat is settled at once: stamping costs a stat, never a read of the file's bytes."""
        self.write("plate.exr", b"x" * 4096)
        image = self.image("plate.exr", "FILE")
        with mock.patch.object(users.change_keys, "_digest", side_effect=AssertionError("bytes read")):
            before = users.file_stamp(image)
            self.assertEqual(users.file_stamp(image), before)
        self.assertEqual(users.KEYS.hashed(), {})

    def test_a_settled_digest_is_read_once_per_version(self):
        self.write("plate.exr", b"x" * 4096)
        image = self.image("plate.exr", "FILE")
        reads = []
        digest = users.change_keys._digest
        with mock.patch.object(users.change_keys, "_digest", lambda *args: reads.append(args[0]) or digest(*args)):
            with unsettled():
                users.file_stamp(image)
            for _ in range(5):
                users.file_stamp(image)
        self.assertEqual(len(reads), 2, "once while unsettled, once when the clock passed it, never again")

    def test_hashing_stops_at_the_budget(self):
        for name in ("a.png", "b.png"):
            self.write(name, b"y" * 100)
        budget = [150]
        with unsettled():
            users.file_stamp(self.image("a.png", "FILE"), budget=budget)
            users.file_stamp(self.image("b.png", "FILE"), budget=budget)   # Crosses it: read whole.
            with self.assertRaises(users.Deferred):
                users.file_stamp(self.image("a.png", "FILE"), budget=budget)
        self.assertEqual(budget, [-50])

    def test_a_respelled_path_to_the_same_file_keeps_its_stamp(self):
        """Bake-buffer review: a stamp keyed on the stored spelling changed when Make Paths Relative or a remapping
        save respelled it, with nothing written."""
        self.write("wood.png", b"wood")
        (self.folder / "sub").mkdir()
        self.addCleanup((self.folder / "sub").rmdir)
        before = users.file_stamp(self.image("wood.png", "FILE"))
        self.assertEqual(users.file_stamp(self.image("sub/../wood.png", "FILE")), before)

    def test_a_file_reached_through_a_linked_folder_keeps_its_stamp(self):
        """Break-it round 3, L4: a stamp keyed on the path changed when the same file was named another way (a
        linked folder; a case respelling where the volume ignores case); it is keyed by device and inode."""
        self.write("wood.png", b"wood")
        link = self.folder / "linked"
        try:
            link.symlink_to(self.folder, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this account can't make a symbolic link here")
        self.addCleanup(link.unlink)
        before = users.file_stamp(self.image("wood.png", "FILE"))
        self.assertEqual(users.file_stamp(self.image("linked/wood.png", "FILE")), before)
        self.write("tile.png", b"tile")   # Round 4, L1: first seen in its clock's step, in its bytes' form.
        with unsettled():
            hashed = users.file_stamp(self.image("tile.png", "FILE"))
        self.assertEqual(users.file_stamp(self.image("linked/tile.png", "FILE")), hashed, "one form per file")

    def test_generated_and_packed_images_have_no_files(self):
        self.assertIsNone(users.file_stamp(self.image("x.png", "GENERATED")))
        packed = self.image("x.png", "FILE")
        packed.packed_file = object()
        self.assertIsNone(users.file_stamp(packed))


if __name__ == "__main__":
    unittest.main()
