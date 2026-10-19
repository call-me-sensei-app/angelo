# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The names Blender gave a script's data-blocks in place of the names asked for (``script_renames``), on readings
and observers built by hand.

Host only: each reading is what ``view_scene_check._inventory`` returns (session UIDs as bytes, names, UIDs left
out) with the linked UIDs and overrides' reference names beside it; the observers (``script_rename_watch``) run on
stand-in types. test_blender_script_renames.py checks the same rules against Blender 5.2's own naming, through the
real executor.
"""

from array import array
import json
import sys
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import model_results, script_after, script_rename_watch, script_renames  # noqa: E402
from angelo_agent.script_rename_watch import Names  # noqa: E402
from angelo_agent.script_renames import renamed, shown, suffixed  # noqa: E402


def reading(collections, linked=None, skip=None, expected=None):
    """``(inventory, linked, expected)`` from ``{collection: [(uid, name), ...]}``."""
    inventory = {name: (array("q", [uid for uid, _name in rows]).tobytes(), tuple(row[1] for row in rows),
                        frozenset((skip or {}).get(name, ()))) for name, rows in collections.items()}
    return inventory, {name: frozenset(uids) for name, uids in (linked or {}).items()}, expected or {}


def found(before, after, leave=frozenset(), **kwargs):
    return renamed(reading(before), reading(after, **kwargs), leave=leave)


NO_INVENTORY = lambda *_arguments: {}  # noqa: E731  ``_reading`` (patched) returns the reading itself.
BPY = SimpleNamespace(data=SimpleNamespace(bl_rna=SimpleNamespace(properties=())))  # ``_reading`` is patched.
FACTORY = {"objects": [(1, "Camera"), (2, "Cube"), (3, "Light")], "cameras": [(11, "Camera")],
           "materials": [(21, "Material")], "worlds": [(31, "World")]}


class RuleTests(unittest.TestCase):
    def test_a_new_data_block_numbered_because_its_name_was_taken_is_listed(self):
        """The class of the recorded mini-camera misses (typed there): a new "Camera" beside the factory one."""
        after = dict(FACTORY, objects=FACTORY["objects"] + [(4, "Camera.001")],
                     cameras=FACTORY["cameras"] + [(12, "Camera.001")])
        self.assertEqual(found(FACTORY, after), {"objects": ["Camera.001"], "cameras": ["Camera.001"]})

    def test_a_material_created_twice_by_one_script(self):
        """The class of the recorded castle miss (a typed program there): a material under a name already taken."""
        after = dict(FACTORY, materials=FACTORY["materials"] + [(22, "Stone"), (23, "Stone.001")])
        self.assertEqual(found(FACTORY, after), {"materials": ["Stone.001"]})

    def test_an_existing_data_block_renamed_into_a_taken_name(self):
        before = {"objects": [(1, "Gate"), (2, "Door")]}
        self.assertEqual(found(before, {"objects": [(1, "Gate"), (2, "Gate.001")]}), {"objects": ["Gate.001"]})

    def test_a_holder_pushed_aside_by_a_forced_rename_is_listed(self):
        """``ID.rename(name, mode='ALWAYS')``: Blender renames the data-block that held the name."""
        before = {"objects": [(1, "Holder"), (2, "Mover")]}
        self.assertEqual(found(before, {"objects": [(1, "Holder.001"), (2, "Holder")]}),
                         {"objects": ["Holder.001"]})

    def test_a_holder_the_script_deleted_after_the_clash_still_caused_it(self):
        before = {"objects": [(1, "Rig")]}
        self.assertEqual(found(before, {"objects": [(2, "Rig.001")]}), {"objects": ["Rig.001"]})

    def test_a_name_recreated_after_the_script_deleted_its_holder_is_listed_again(self):
        """The same name as before, on a new data-block: it was numbered again in this script."""
        before = {"objects": [(1, "Lamp"), (2, "Lamp.001")]}
        self.assertEqual(found(before, {"objects": [(1, "Lamp"), (3, "Lamp.001")]}), {"objects": ["Lamp.001"]})

    def test_the_scripts_own_numbered_names_are_not_listed(self):
        """Nobody holds "Post": "Post.001" and "Post.002" are names the script chose."""
        after = {"objects": [(5, "Post.001"), (6, "Post.002")]}
        self.assertEqual(found({"objects": []}, after), {})

    def test_a_name_freed_first_is_not_listed(self):
        before = {"objects": [(1, "Camera")], "materials": [(21, "Stone")]}
        after = {"objects": [(2, "Camera")], "materials": [(21, "Stone old"), (22, "Stone")]}
        self.assertEqual(found(before, after), {})

    def test_its_own_former_name_is_never_its_holder(self):
        self.assertEqual(found({"objects": [(1, "Post")]}, {"objects": [(1, "Post.001")]}), {})

    def test_numbered_names_from_before_the_script_are_not_repeated(self):
        before = {"objects": [(1, "Cube"), (2, "Cube.001")], "meshes": [(41, "Cube"), (42, "Cube.001")]}
        after = {"objects": [(1, "Cube"), (2, "Cube.001"), (3, "Wall")],
                 "meshes": [(41, "Cube"), (42, "Cube.001"), (43, "Wall")]}
        self.assertEqual(found(before, after), {})

    def test_linked_data_blocks_are_never_listed_nor_holders(self):
        """A linked name is unique only within its library; a local name never clashes with it."""
        before = {"materials": [(21, "Stone"), (90, "Brick")]}
        after = {"materials": [(21, "Stone"), (90, "Brick"), (91, "Stone.001"), (22, "Brick.001")]}
        self.assertEqual(found(before, after), {"materials": ["Stone.001", "Brick.001"]}, "Unlinked: both")
        self.assertEqual(renamed(reading(before, linked={"materials": {90}}),
                                 reading(after, linked={"materials": {90, 91}})), {})

    def test_an_override_is_judged_by_its_reference_name(self):
        """Regression: overrides of linked "Rock" and "Rock.001" were listed though Blender renamed nothing."""
        before = {"objects": [(90, "Rock"), (91, "Rock.001")]}
        after = {"objects": [(90, "Rock"), (91, "Rock.001"), (1, "Rock"), (2, "Rock.001"), (3, "Rock.002")]}
        self.assertEqual(renamed(reading(before, linked={"objects": {90, 91}}),
                                 reading(after, linked={"objects": {90, 91}},
                                         expected={"objects": {1: "Rock", 2: "Rock.001", 3: "Rock.001"}})),
                         {"objects": ["Rock.002"]}, "The third asked for Rock.001 and Blender gave Rock.002")

    def test_data_blocks_judged_elsewhere_are_left_to_it(self):
        """Brought in from a file or named by an assignment the script made: never by the name's form."""
        after = {"objects": [(1, "Rock"), (2, "Rock.001"), (3, "Rock.002")]}
        self.assertEqual(found({"objects": []}, after), {"objects": ["Rock.001", "Rock.002"]})
        self.assertEqual(found({"objects": []}, after, leave={1, 2, 3}), {})

    def test_render_result_and_viewer_images_are_left_out(self):
        before = {"images": [(51, "Render Result")]}
        after = {"images": [(51, "Render Result"), (52, "Render Result.001")]}
        self.assertEqual(renamed(reading(before, skip={"images": {51, 52}}),
                                 reading(after, skip={"images": {51, 52}})), {})

    def test_an_incomplete_reading_compares_nothing(self):
        inventory, linked, expected = reading({"objects": [(1, "Camera"), (2, "Camera.001")]})
        broken = {"objects": (inventory["objects"][0][:8], inventory["objects"][1], frozenset())}
        self.assertEqual(renamed(reading({"objects": [(1, "Camera")]}), (broken, linked, expected)), {})

    def test_a_document_the_script_replaced_states_nothing(self):
        """A file load gives every data-block a new session UID (Undo keeps them): its numbered names are not new."""
        before = {"scenes": [(100, "Scene")], "objects": [(1, "Rock")]}
        loaded = {"scenes": [(200, "Scene")], "objects": [(5, "Rock"), (6, "Rock.001")]}
        self.assertEqual(found(before, loaded), {})
        new_scene = {"scenes": [(201, "Scene")], "objects": [(1, "Rock"), (6, "Rock.001")]}
        self.assertEqual(found(before, new_scene), {"objects": ["Rock.001"]}, "Its objects are still there")

    def test_blenders_number_form(self):
        for name, base in (("Wall.001", "Wall"), ("Wall.999", "Wall"), ("Wall.1000", "Wall"),
                           ("Wall.999999999", "Wall"), ("A.B.012", "A.B"), ("Wall .010", "Wall ")):
            self.assertEqual(suffixed(name), base, name)
        for name in ("Wall", "Wall.1", "Wall.01", "Wall.000", "Wall.0001", "Wall.0999", "Wall.1000000000",
                     "Wall.", ".001", "Wall.0x1", "Wall.١٢٣", "Wall_001", "Wall.001a"):
            self.assertIsNone(suffixed(name), name)

    def test_a_long_name_blender_cut_instead_of_numbering(self):
        """Blender 5.2 names are at most 255 bytes: a taken name of 252 bytes or more is cut by a character."""
        long = "B" * 255
        after = {"objects": [(1, long), (2, long[:-1]), (3, long[:-2])]}
        self.assertEqual(found({"objects": [(1, long)]}, after), {"objects": [long[:-1], long[:-2]]})
        sixty_three = "A" * 63
        after = {"objects": [(1, sixty_three), (2, sixty_three + ".001")]}
        self.assertEqual(found({"objects": [(1, sixty_three)]}, after), {"objects": [sixty_three + ".001"]})
        wide = "é" * 127 + "x"   # 255 bytes: a cut takes the "x".
        self.assertEqual(found({"objects": [(1, wide)]}, {"objects": [(1, wide), (2, wide[:-1])]}),
                         {"objects": [wide[:-1]]})

    def test_a_long_name_is_a_cut_only_where_blender_could_have_cut_it(self):
        """Regression: beside a 252-byte "B..." a 200-byte "B..." of the script's was listed. A cut is one
        character shorter than a held name of 252 bytes or more, every step of the chain held."""
        held = "B" * 252
        for name in ("B" * 200, "B" * 250, "B" * 20):
            self.assertEqual(found({"objects": [(1, held)]}, {"objects": [(1, held), (2, name)]}), {}, len(name))
        self.assertEqual(found({"objects": [(1, held)]}, {"objects": [(1, held), (2, "B" * 251)]}),
                         {"objects": ["B" * 251]})
        self.assertEqual(found({"objects": [(1, "B" * 251)]}, {"objects": [(1, "B" * 251), (2, "B" * 250)]}), {},
                         "A 251-byte name is numbered, never cut")


class AskTests(unittest.TestCase):
    """Names the script assigned (``script_rename_watch``): the name asked for decides."""

    class Block:
        def __init__(self, uid, name, library=None):
            self.session_uid, self.name, self.library = uid, name, library
            self.bl_rna = SimpleNamespace(identifier="Object", base=None)

    def asks(self, *rows):
        return {block.session_uid: (block, asked, got) for block, asked, got in rows}

    def test_a_rename_into_a_taken_name_that_changed_nothing_is_listed(self):
        """Regression: objects["Gate.001"].name = "Gate" while "Gate" exists keeps "Gate.001", unseen by names."""
        gate = self.Block(2, "Gate.001")
        decided, listed = script_renames._asks(self.asks((gate, "Gate", "Gate.001")), lambda block: "objects")
        self.assertEqual((decided, listed), ({2}, {"objects": ["Gate.001"]}))

    def test_a_name_the_script_got_is_its_own_and_a_cut_one_is_blenders(self):
        own, cut = self.Block(3, "Post.001"), self.Block(4, "X" * 255)
        decided, listed = script_renames._asks(self.asks((own, "Post.001", "Post.001"), (cut, "X" * 300, "X" * 255)),
                                               lambda block: "objects")
        self.assertEqual((decided, listed), ({3, 4}, {"objects": ["X" * 255]}))

    def test_a_block_renamed_again_by_other_means_or_removed_is_left_to_the_names_form(self):
        moved, linked = self.Block(5, "Rock.004"), self.Block(6, "Lib", library=object())

        class Removed:
            session_uid = 7

            @property
            def name(self):
                raise ReferenceError("removed")
        decided, listed = script_renames._asks(
            self.asks((moved, "Rock", "Rock.003"), (linked, "Lib", "Lib.001"), (Removed(), "A", "A.001")),
            lambda block: "objects")
        self.assertEqual((decided, listed), (set(), {}))


class FieldTests(unittest.TestCase):
    def test_runs_of_consecutive_numbers_are_one_entry(self):
        cubes = ["Cube.%03d" % number for number in range(1, 13)]
        field = shown({"objects": ["Camera.001"] + cubes, "meshes": cubes[:2] + ["Cube.005"]})
        self.assertEqual(field, {"objects": ["Camera.001", "Cube.001 to Cube.012 (12)"],
                                 "meshes": ["Cube.001", "Cube.002", "Cube.005"]})
        self.assertEqual(shown({"objects": ["Post.001", "Post.002", "Post.003", "Post.005", "Post.1000"]}),
                         {"objects": ["Post.001 to Post.003 (3)", "Post.005", "Post.1000"]})

    def test_kinds_looked_up_by_name_come_before_object_data_and_the_cap_counts_the_rest(self):
        """Recorded scripts looked objects up by name in 764 of 1,279, meshes in 7: noise never hides a material."""
        meshes = ["Part %02d.001" % number for number in range(script_renames.MAX_ENTRIES + 3)]
        field = shown({"meshes": meshes, "materials": ["Stone.001"], "objects": ["Rock.%03d" % n for n in range(1, 6)]})
        self.assertEqual(list(field), ["objects", "materials", "meshes", "omitted"])
        self.assertEqual(field["objects"], ["Rock.001 to Rock.005 (5)"])
        self.assertEqual(field["materials"], ["Stone.001"])
        self.assertEqual(len(field["meshes"]), script_renames.MAX_ENTRIES - 2)
        self.assertEqual(field["omitted"], {"meshes": 5})
        self.assertLess(script_renames.ORDER.index("collections"), script_renames.ORDER.index("meshes"))
        self.assertEqual(set(script_renames.ORDER), set(script_renames.COLLECTIONS))

    def test_kinds_left_unread_are_named(self):
        self.assertEqual(shown({}, ["meshes"]), {"not_checked": ["meshes"]})

    def test_bytes(self):
        """The usual case stays small; the cap bounds the rest."""
        usual = shown({"objects": ["Camera.001"], "cameras": ["Camera.001"]})
        self.assertLess(len(json.dumps(usual, separators=(",", ":"))), 60)


class Collection(list):
    """A ``bpy.data`` collection as ``view_scene_check._inventory`` reads it; ``walks`` counts ``len`` calls."""

    walks = 0

    def __len__(self):
        type(self).walks += 1
        return super().__len__()

    def keys(self):
        return [block.name for block in self]

    def foreach_get(self, name, buffer):
        if len(buffer) != list.__len__(self):
            raise ValueError("size")
        buffer[:] = array(buffer.typecode, [getattr(block, name) for block in self])


def data(libraries=(), **collections):
    return SimpleNamespace(libraries=list(libraries), **{name: Collection(
        SimpleNamespace(session_uid=uid, name=name_, library=library, override_library=override, type="")
        for uid, name_, library, override in ((row + (None, None))[:4] for row in rows))
        for name, rows in collections.items()})


class ReadingTests(unittest.TestCase):
    def before(self, document, limit):
        with patch.dict(sys.modules, bpy=SimpleNamespace(data=document)), \
                patch.object(script_renames, "MAX_NAMES", limit):
            return script_renames.before()

    def test_kinds_looked_up_by_name_are_always_read_and_object_data_within_the_limit(self):
        """Regression: 52,000 objects and 20,000 meshes left the objects unread and read the meshes."""
        document = data(objects=[(1, "Gate"), (2, "Wall"), (3, "Rock")], meshes=[(11, "Gate"), (12, "Wall")],
                        materials=[(21, "Stone")], lights=[(31, "Sun")])
        names = self.before(document, 2)
        self.assertEqual(list(names.reading[0]), [name for name in script_renames.NAMED
                                                  if name in ("objects", "materials")])
        names = self.before(document, 5)
        self.assertEqual(set(names.reading[0]), {"objects", "materials", "lights"}, "Meshes would pass it; lights fit")
        names = self.before(document, 7)
        self.assertEqual(set(names.reading[0]), {"objects", "materials", "meshes", "lights"})
        self.assertEqual(array("q", names.reading[0]["objects"][0]).tolist(), [1, 2, 3])

    def test_a_kind_past_the_limit_is_counted_not_read(self):
        document = data(objects=[(1, "Gate")], meshes=[(11, "A"), (12, "B")])
        document.meshes.keys = lambda: self.fail("names read past the limit")
        self.before(document, 2)

    def test_a_file_that_links_libraries_has_its_linked_and_override_data_blocks_read(self):
        library = object()
        override = SimpleNamespace(reference=SimpleNamespace(name="Stone"))
        document = data([library], materials=[(21, "Stone.001", None, override), (90, "Stone", library)])
        names = self.before(document, 100)
        self.assertEqual(names.reading[1], {"materials": frozenset({90})})
        self.assertEqual(names.reading[2], {"materials": {21: "Stone"}})

    def test_after_the_script_the_same_kinds_are_read_and_the_unread_ones_named(self):
        before = data(objects=[(1, "Gate")], meshes=[(11, "Gate"), (12, "Wall")])
        after = data(objects=[(1, "Gate"), (2, "Gate.001")], meshes=[(11, "Gate"), (12, "Wall"), (13, "Gate.001")])
        names = self.before(before, 2)
        with patch.dict(sys.modules, bpy=SimpleNamespace(data=after)), \
                patch.object(script_renames, "_kinds", lambda _data: lambda block: None):
            result = script_renames.attach({"success": True}, names)
        self.assertEqual(result[script_renames.FIELD], {"objects": ["Gate.001"], "not_checked": ["meshes"]})


class AttachTests(unittest.TestCase):
    def setUp(self):
        self.after = reading(dict(FACTORY, materials=FACTORY["materials"] + [(22, "Stone"), (23, "Stone.001")]))

    def attach(self, result, names=None, after=None, error=None):
        names = Names(reading(FACTORY)) if names is None else names

        def read(_data, _inventory):
            if error is not None:
                raise error
            return after or self.after
        with patch.object(script_renames, "_reading", read), patch.object(script_renames, "_inventory", NO_INVENTORY), \
                patch.dict(sys.modules, bpy=BPY):
            return script_renames.attach(result, names)

    def test_a_completed_script_states_the_names(self):
        self.assertEqual(self.attach({"success": True})[script_renames.FIELD], {"materials": ["Stone.001"]})

    def test_nothing_renamed_or_nothing_read_adds_nothing(self):
        self.assertNotIn(script_renames.FIELD, self.attach({"success": True}, after=reading(FACTORY)))
        self.assertNotIn(script_renames.FIELD, script_renames.attach({"success": True}, None))
        self.assertNotIn(script_renames.FIELD, self.attach({"success": True}, error=RuntimeError("freed")))
        unsure = Names(reading(FACTORY))
        unsure.unsure = True
        self.assertNotIn(script_renames.FIELD, self.attach({"success": True}, names=unsure))

    def test_data_blocks_from_files_are_left_out(self):
        names = Names(reading(FACTORY))
        names.from_files.update({22, 23})
        self.assertNotIn(script_renames.FIELD, self.attach({"success": True}, names=names))

    def test_only_a_completed_script_gets_it_and_the_model_reads_it(self):
        def run(result):
            with patch.object(script_renames, "_reading", lambda *_arguments: self.after), \
                    patch.object(script_renames, "_inventory", NO_INVENTORY), patch.dict(sys.modules, bpy=BPY):
                # A window without a scene: ``after`` cannot be read and is left out; the field stands alone.
                return script_after.attach(SimpleNamespace(), result, names=Names(reading(FACTORY)))
        changes = {"added": [], "modified": [], "removed": []}
        for failed in ({"success": False, "error": "boom", "changes": changes},
                       {"success": True, "error": "rolled back", "changes": changes}):
            self.assertNotIn(script_renames.FIELD, run(dict(failed)))
        completed = run({"success": True, "changes": changes})
        self.assertNotIn("after", completed)
        self.assertEqual(completed[script_renames.FIELD], {"materials": ["Stone.001"]})
        for follows in (True, False):
            shown_to_model = model_results.model_result("angelo_execute_python", completed, observation_follows=follows)
            self.assertEqual(shown_to_model[script_renames.FIELD], {"materials": ["Stone.001"]})

    def test_the_executor_reads_the_names_before_the_script_and_watches_only_its_exec(self):
        source = (Path(script_renames.__file__).parent / "blender_tools.py").read_text()
        start = source.index("    def execute(self, name, arguments):")
        execute = source[start:source.index("\n    def ", start + 1)]
        self.assertLess(execute.index("script_after.Before(self.window, bpy)"), execute.index("self._execute(name, arguments)"))
        self.assertIn("prior.attach(self.window, result, arguments[\"code\"])", execute)
        after = (Path(script_renames.__file__).parent / "script_after.py").read_text()
        before = after[after.index("class Before:"):after.index("\ndef attach(")]
        self.assertIn("script_renames.before()", before)
        self.assertIn("names=self.names", before)
        self.assertIn("redirect_stderr(output), observe:\n                        exec(code, namespace)", source)
        observing = after[after.index("class Observing:"):after.index("\ndef attach(")]
        self.assertIn("script_rename_watch.watching(bpy, getattr(prior, \"names\", None))", observing)


class Struct:
    """A stand-in RNA struct type whose ``__setattr__`` the watch may replace."""

    def __init__(self, uid, name):
        object.__setattr__(self, "session_uid", uid)
        object.__setattr__(self, "name", name)


class Operator:
    def __init__(self, properties, run):
        self._properties, self._run = properties, run

    def __call__(self, *args, **kwargs):
        return self._run()

    def get_rna_type(self):
        return SimpleNamespace(properties={name: None for name in self._properties})

    def poll(self):
        return True


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.objects = Collection([SimpleNamespace(session_uid=1, name="Cube")])
        self.made = []

        def create(module, function):
            reads = function == "obj_import"
            made = SimpleNamespace(session_uid=50 if reads else 51, name="Plank.001" if reads else "Cube.001")
            return Operator(("filepath", "directory", "files") if reads else ("size",),
                            lambda: self.objects.append(made))

        objects = self.objects

        class Libraries:
            def load(self, *args, **kwargs):
                class Context:
                    def __enter__(self):
                        return ("from", "to")

                    def __exit__(self, *exception):
                        objects.append(SimpleNamespace(session_uid=60, name="Rock.001"))
                        return False
                return Context()

        class LibraryCollection:
            """As ``bpy_prop_collection``: binds its type's method only when it is the original one."""

            def __getattr__(self, attribute):
                found = vars(Libraries)[attribute]
                return found.__get__(self) if found is vars(LibraryCollection)["original"] else found
        LibraryCollection.original = Libraries.load

        class ID(Struct):
            pass
        self.ID, self.Libraries, self.create = ID, Libraries, create
        self.bpy = SimpleNamespace(ops=SimpleNamespace(_op_create_function=create),
                                   types=SimpleNamespace(BlendDataLibraries=Libraries, ID=ID),
                                   data=SimpleNamespace(objects=self.objects, libraries=LibraryCollection()))

    def test_file_reads_assignments_and_appends_are_observed_and_everything_is_put_back(self):
        names = Names(None)
        block = self.ID(7, "Gate.001")
        with script_rename_watch.watching(self.bpy, names):
            self.bpy.ops._op_create_function("wm", "obj_import")(filepath="planks.obj")
            self.bpy.ops._op_create_function("mesh", "primitive_cube_add")(size=2)
            with self.bpy.data.libraries.load("rocks.blend", link=False) as pair:
                self.assertEqual(pair, ("from", "to"))
            block.name = "Gate"
            block.location = (1, 2, 3)
        self.assertEqual(names.from_files, {50, 60})
        self.assertEqual({uid: (asked, got) for uid, (_block, asked, got) in names.asks.items()}, {7: ("Gate", "Gate")})
        self.assertFalse(names.unsure)
        self.assertIs(self.bpy.ops._op_create_function, self.create)
        self.assertNotIn("__setattr__", vars(self.ID))
        self.assertNotEqual(self.bpy.types.BlendDataLibraries.load.__qualname__, "_Watch._library_loads.<locals>.load")
        block.name = "After"
        self.assertEqual(len(names.asks), 1, "Nothing is observed after the script")

    def test_a_load_the_script_keeps_never_puts_the_watch_back(self):
        """During the script ``bpy.data.libraries.load`` is the wrapper itself; called later (a timer,
        a handler), it must run the load as installed then, never re-install itself or record into old names."""
        original, names = vars(self.Libraries)["load"], Names(None)
        with script_rename_watch.watching(self.bpy, names):
            kept = self.bpy.data.libraries.load
        with kept("rocks.blend", link=False):
            pass
        self.assertIs(vars(self.Libraries)["load"], original)
        self.assertEqual(names.from_files, set(), "Nothing is recorded after the script")
        later = Names(None)
        with script_rename_watch.watching(self.bpy, later):
            pass
        self.assertIs(vars(self.Libraries)["load"], original, "The next script restores Blender's own load")

    def test_a_file_operator_the_script_keeps_records_into_the_script_running_when_called(self):
        """An operator kept past its script records into no finished script's names."""
        first, second = Names(None), Names(None)
        with script_rename_watch.watching(self.bpy, first):
            kept = self.bpy.ops._op_create_function("wm", "obj_import")
        with script_rename_watch.watching(self.bpy, second):
            kept(filepath="planks.obj")
        kept(filepath="planks.obj")   # And outside any script.
        self.assertEqual((first.from_files, second.from_files), (set(), {50}))

    def test_a_script_that_raises_still_puts_everything_back(self):
        names = Names(None)
        with self.assertRaises(KeyError):
            with script_rename_watch.watching(self.bpy, names):
                raise KeyError("the script's own")
        self.assertIs(self.bpy.ops._op_create_function, self.create)
        self.assertNotIn("__setattr__", vars(self.ID))

    def test_an_observer_that_cannot_be_put_in_place_states_nothing(self):
        del self.bpy.types.BlendDataLibraries
        names = Names(None)
        with script_rename_watch.watching(self.bpy, names):
            pass
        self.assertTrue(names.unsure)
        self.assertIs(self.bpy.ops._op_create_function, self.create)


if __name__ == "__main__":
    unittest.main()
