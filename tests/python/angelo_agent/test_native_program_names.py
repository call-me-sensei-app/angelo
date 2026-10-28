# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object names in program order (program_names): staging, verification and commit.

The real ``object_rename``, ``object_delete``, ``camera_create``,
``create_object`` and ``object_duplicate`` kernels run against a fake ``bpy``
whose Main gives names as Blender does (a taken name gets the lowest free
``.NNN`` suffix), so the plans of a "replace the camera"
benchmark task replay exactly: before the fix a name the program had freed by
renaming or deleting the old camera still came out ``Camera.001`` or was
refused ``NAME_TAKEN``. Commit's ``apply_names`` runs on the same fakes, with
its journal replayed in reverse as commit's rollback does. The compiled-app
path is covered by test_blender_object_management.py.
"""

import contextlib
import importlib
import itertools
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy  # noqa: F401  The kernels import it; loaded once here, not again inside each fake world.

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from angelo_agent import program_names as names  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402

CAMERA, CUBE, LAMP, EXTRA = ("scn:" + "a" * 32 + ":" + str(index) for index in (115, 116, 117, 118))
_pointers = itertools.count(1)


class FakeMain:
    """Unique local object names, as Blender gives them: a taken name gets the lowest free ``.NNN`` of its base."""

    def __init__(self):
        self.objects = {}

    def give(self, obj, wanted):
        holder = self.objects.get(wanted)
        name = wanted
        if holder is not None and holder is not obj:
            base = re.sub(r"\.\d+$", "", wanted)  # BLI_string_split_name_number: any digits after the last dot.
            for number in itertools.count(1):
                name = "%s.%03d" % (base, number)
                if self.objects.get(name) in (None, obj):
                    break
        if obj._name is not None:
            del self.objects[obj._name]
        obj._name = name
        self.objects[name] = obj


class FakeAnimation:
    def __init__(self):
        self.action, self.action_slot, self.last_slot_identifier = None, None, ""


class FakeObject:
    id_type = "OBJECT"  # As Blender's ID.id_type: checks read an object's name through it (program_verification).

    def __init__(self, main, name, kind="EMPTY", data=None, collections=()):
        self._name, self.main, self.type, self.data = None, main, kind, data
        self.pointer, self.users_collection, self.animation_data = next(_pointers), list(collections), None
        main.give(self, name)

    name = property(lambda self: self._name, lambda self, value: self.main.give(self, value))

    def as_pointer(self):
        return self.pointer

    def copy(self):
        return FakeObject(self.main, self.name, self.type, self.data, self.users_collection)

    def animation_data_create(self):
        self.animation_data = self.animation_data or FakeAnimation()
        return self.animation_data


class FakeCamera:
    def __init__(self, name):
        self.name, self.pointer, self.animation_data = name, next(_pointers), None
        self.type, self.lens, self.ortho_scale, self.sensor_fit = "PERSP", 50., 6., "AUTO"
        self.sensor_width, self.sensor_height, self.clip_start, self.clip_end = 36., 24., .1, 1000.

    def as_pointer(self):
        return self.pointer

    def copy(self):
        return FakeCamera(self.name)


class FakeCollection:
    def __init__(self, *children):
        self.children, self.pointer = list(children), next(_pointers)

    def as_pointer(self):
        return self.pointer


class Context:
    """The staged-kernel context surface these kernels use; ``objects`` are staged copies by handle.

    As the real StagedActionContext, a reference deleted earlier in the program is refused.
    """

    def __init__(self, extras, objects):
        self.extras, self.objects, self.aliases = extras, dict(objects), {}

    def track_id(self, block):
        return block

    def require_private(self, block):
        return block

    def add_object(self, obj, alias=None):
        if alias:
            self.aliases["$" + alias] = obj
        return obj

    def resolve(self, reference):
        if reference in self.extras.get("deleted", ()):
            raise RefusalError("This object is deleted earlier in the same program", code="VALIDATION")
        return self.aliases[reference] if reference.startswith("$") else self.objects[reference]


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def camera(name, alias="cam"):
    return op("camera_create", name=name, alias=alias, location=[6., -8., 4.], rotation=[0., 0., 0.],
              settings={"lens": 50.})


def empty(name, alias):
    return op("create_object", type="EMPTY", name=name, alias=alias, display="ARROWS", display_size=1.,
              location=[0., 0., 0.], rotation=[0., 0., 0.], scale=[1., 1., 1.])


# The kernels bind ``bpy`` when imported. They are imported once against this fake, whose Main is the current
# World's, then kept: re-importing every action module for each case would dominate the differential fuzz.
_FAKE = NS(main=None)
_KERNELS = {}


def _kernels():
    if not _KERNELS:
        new = lambda name, data: FakeObject(_FAKE.main, name, "CAMERA" if isinstance(data, FakeCamera) else "EMPTY", data)
        bpy = NS(data=NS(objects=NS(new=new, get=lambda name: _FAKE.main.objects.get(name)), cameras=NS(new=FakeCamera),
                         user_map=lambda subset=None: {}),
                 types=NS(Object=FakeObject), context=NS(), ops=NS(), app=NS(build_options=NS(opensubdiv=True)))
        package = dict(vars(angelo_agent))
        with patch.dict(sys.modules, {"bpy": bpy, "mathutils": NS(Vector=tuple, Matrix=object, Quaternion=object),
                                      "bmesh": NS()}):
            for module in [name for name in sys.modules if name.startswith("angelo_agent.action_")]:
                del sys.modules[module]  # patch.dict restores the originals afterwards.
            _KERNELS["management"] = importlib.import_module("angelo_agent.action_object_management")
            _KERNELS["camera"] = importlib.import_module("angelo_agent.action_camera_light")
        for key in set(vars(angelo_agent)) - set(package):
            delattr(angelo_agent, key)
        for key, value in package.items():
            setattr(angelo_agent, key, value)
    return _KERNELS


class World:
    """The factory scene's camera, cube and light, live and staged, with the kernels bound to a fake bpy."""

    def __init__(self, test=None):
        self.main = _FAKE.main = FakeMain()
        self.collection = FakeCollection()
        self.scene = NS(collection=FakeCollection(self.collection))
        self.live = {handle: FakeObject(self.main, name, kind, collections=[self.collection])
                     for handle, name, kind in ((CAMERA, "Camera", "CAMERA"), (CUBE, "Cube", "EMPTY"),
                                                (LAMP, "Light", "LIGHT"))}
        staged = {handle: FakeObject(self.main, "__angelo_copy_ab12cd34_" + obj.name, obj.type)
                  for handle, obj in self.live.items()}
        self.context = Context({"live_names": {handle: obj.name for handle, obj in self.live.items()}}, staged)
        kernels = _kernels()
        self.management, self.camera = kernels["management"], kernels["camera"]

    def add_live(self, handle, name, kind="CAMERA"):
        self.live[handle] = FakeObject(self.main, name, kind, collections=[self.collection])
        self.context.objects[handle] = FakeObject(self.main, "__angelo_copy_ab12cd34_" + name, kind)
        self.context.extras["live_names"][handle] = name

    def share(self, *deleted):
        """Another scene links these objects, recorded as stage creation does for the program's deletions."""
        for handle in deleted:
            self.live[handle].users_collection.append(FakeCollection())
        names.record_kept_elsewhere(self.context.extras, self.live, deleted, [self.scene.collection, self.collection])

    def plan(self, operations):
        """What stage creation does before any kernel runs: the names released later (may refuse NAME_AMBIGUOUS)."""
        extras = self.context.extras
        forward = names.plan_names(operations, {handle: obj.name for handle, obj in self.live.items()},
                                   extras.get(names.KEPT_ELSEWHERE, ()))
        if forward:
            extras[names.FORWARD] = forward
        extras[names.REPEATS] = names.held_repeats(operations, self.main.objects.get)

    def stage(self, *operations):
        """Plan, then run every operation in order, as a staged program does."""
        self.plan(list(operations))
        return [self.run(operation) for operation in operations]

    def run(self, operation):
        _FAKE.main = self.main
        kind, target, context = operation["kind"], operation["target"], self.context
        if kind == "camera_create":
            return self.camera.create(context, operation)
        if kind in {"create_object", "object_duplicate"}:
            return getattr(self.management, {"create_object": "create_object", "object_duplicate": "duplicate"}[kind])(
                context, operation)
        kernel = {"object_rename": self.management.rename, "object_delete": self.management.delete}[kind]
        return kernel(context, context.resolve(target), operation)

    def commit(self):
        """commit's naming step; returns the journal it would roll back, after rolling back a refusal itself."""
        journal = []
        stage = NS(extras=self.context.extras, originals=self.live)
        try:
            names.apply_names(stage, self.scene, list(self.context.aliases.values()), journal)
        except ProgramError:
            roll_back(journal)
            raise
        return journal

    def names_of(self, *handles):
        return [self.live[handle].name for handle in handles]

    def snapshot(self):
        return {id(obj): obj.name for obj in list(self.live.values()) + list(self.context.aliases.values())}


def roll_back(journal):
    for holder, field, value in reversed(journal):
        setattr(holder, field, value)


class BenchmarkPlanTests(unittest.TestCase):
    """The plans of "add a camera ... make it the scene camera, then delete the old camera"."""

    def setUp(self):
        self.world = World(self)

    def test_round_one_sentence_order_takes_the_name_its_holder_releases_later(self):
        # The plan in the person's order: create, then delete the old camera. Was Camera.001, a failed name check
        # and a lost round; the holder's later deletion releases the name to the request.
        result, _ = self.world.stage(camera("Camera"), op("object_delete", CAMERA))
        created = self.world.context.aliases["$cam"]
        self.assertEqual((result["object"], names.committed_name(self.world.context.extras, created)), ("Camera", "Camera"))
        self.assertEqual(self.world.context.extras["live_names"]["$cam"], "Camera")
        journal = self.world.commit()
        self.assertEqual(created.name, "Camera")
        self.assertTrue(self.world.live[CAMERA].name.startswith(names.TEMPORARY))
        roll_back(journal)
        self.assertEqual(self.world.live[CAMERA].name, "Camera")
        self.assertTrue(created.name.startswith(names.PLACEHOLDER))

    def test_round_two_rename_after_delete_takes_the_freed_name(self):
        self.world.run(camera("Camera_New"))
        self.world.run(op("object_delete", CAMERA))
        result = self.world.run(op("object_rename", "$cam", name="Camera"))  # Was NAME_TAKEN.
        created = self.world.context.aliases["$cam"]
        self.assertEqual((result["object"], result["name"]), ("Camera", "Camera"))
        self.assertTrue(created.name.startswith(names.PLACEHOLDER))
        self.assertIsNone(self.world.main.objects.get("Camera_New"))  # Renamed away for real.
        journal = self.world.commit()
        self.assertEqual(created.name, "Camera")
        self.assertTrue(self.world.live[CAMERA].name.startswith(names.TEMPORARY))  # Removed after commit.
        roll_back(journal)
        self.assertEqual(self.world.live[CAMERA].name, "Camera")
        self.assertTrue(created.name.startswith(names.PLACEHOLDER))

    def test_round_three_rename_old_first_then_create(self):
        self.world.run(op("object_rename", CAMERA, name="Camera_Old"))
        result = self.world.run(camera("Camera"))  # Was Camera.001.
        self.world.run(op("object_delete", CAMERA))
        self.assertEqual(result["object"], "Camera")
        created = self.world.context.aliases["$cam"]
        self.assertEqual(names.committed_name(self.world.context.extras, created), "Camera")
        self.assertEqual(self.world.context.extras["live_names"]["$cam"], "Camera")
        self.world.commit()
        # The old camera is deleted, so its rename is moot; it only gives its name up.
        self.assertEqual(created.name, "Camera")
        self.assertTrue(self.world.live[CAMERA].name.startswith(names.TEMPORARY))

    def test_delete_first_then_create(self):
        self.world.run(op("object_delete", CAMERA))
        self.assertEqual(self.world.run(camera("Camera"))["object"], "Camera")
        self.world.commit()
        self.assertEqual(self.world.context.aliases["$cam"].name, "Camera")

    def test_rename_kept_object_then_create(self):
        self.world.run(op("object_rename", CAMERA, name="Camera_Old"))
        self.world.run(camera("Camera"))
        journal = self.world.commit()
        self.assertEqual((self.world.context.aliases["$cam"].name, self.world.names_of(CAMERA)), ("Camera", ["Camera_Old"]))
        roll_back(journal)
        self.assertEqual(self.world.names_of(CAMERA), ["Camera"])


class ProgramOrderTests(unittest.TestCase):
    def setUp(self):
        self.world = World(self)

    def test_existing_object_takes_a_deleted_objects_name(self):
        self.world.run(op("object_delete", CAMERA))
        self.world.run(op("object_rename", CUBE, name="Camera"))  # Was NAME_TAKEN.
        self.world.commit()
        self.assertEqual(self.world.names_of(CUBE), ["Camera"])
        self.assertTrue(self.world.live[CAMERA].name.startswith(names.TEMPORARY))

    def test_swap_through_a_temporary_name(self):
        for target, name in ((CUBE, "Swap"), (LAMP, "Cube"), (CUBE, "Light")):
            self.world.run(op("object_rename", target, name=name))
        journal = self.world.commit()
        self.assertEqual(self.world.names_of(CUBE, LAMP), ["Light", "Cube"])
        roll_back(journal)
        self.assertEqual(self.world.names_of(CUBE, LAMP), ["Cube", "Light"])

    def test_a_name_not_released_stays_taken(self):
        with self.assertRaisesRegex(RefusalError, "NAME_TAKEN"):
            self.world.run(op("object_rename", CUBE, name="Light"))  # The light keeps it.
        FakeObject(self.world.main, "Undeclared")
        self.assertEqual(self.world.run(camera("Undeclared"))["object"], "Undeclared.001")
        with self.assertRaisesRegex(RefusalError, "NAME_TAKEN"):
            self.world.run(op("create_object", type="EMPTY", name="Cube", alias="pivot", display="ARROWS",
                              display_size=1., location=[0., 0., 0.], rotation=[0., 0., 0.], scale=[1., 1., 1.]))

    def test_a_claimed_name_is_taken_for_later_operations(self):
        self.world.run(op("object_delete", CAMERA))
        self.world.run(camera("Camera"))
        self.assertEqual(self.world.run(camera("Camera", alias="second"))["object"], "Camera.001")
        with self.assertRaisesRegex(RefusalError, "NAME_TAKEN"):
            self.world.run(op("object_rename", CUBE, name="Camera"))
        with self.assertRaisesRegex(RefusalError, "NAME_TAKEN"):
            self.world.run(op("object_rename", "$second", name="Camera"))

    def test_renaming_a_claim_away_gives_it_up(self):
        self.world.run(op("object_delete", CAMERA))
        self.world.run(camera("Camera"))
        self.assertEqual(self.world.run(op("object_rename", "$cam", name="Shot"))["object"], "Shot")
        extras = self.world.context.extras
        self.assertEqual((extras[names.CLAIMS], "$cam" in extras["live_names"]), ({}, False))
        self.assertEqual(self.world.run(op("object_rename", CUBE, name="Camera"))["name"], "Camera")

    def test_named_duplicate_and_created_empty_take_freed_names(self):
        self.world.run(op("object_delete", CAMERA))
        self.world.run(op("object_rename", LAMP, name="Lamp"))
        self.world.run(op("object_duplicate", source=CUBE, alias="twin", name="Camera", linked=False, location=None))
        result = self.world.run(op("create_object", type="EMPTY", name="Light", alias="pivot", display="ARROWS",
                                   display_size=1., location=[0., 0., 0.], rotation=[0., 0., 0.], scale=[1., 1., 1.]))
        self.assertEqual(result["object"], "Light")
        self.world.commit()
        self.assertEqual([self.world.context.aliases[alias].name for alias in ("$twin", "$pivot")], ["Camera", "Light"])
        self.assertEqual(self.world.names_of(LAMP), ["Lamp"])

    def test_derived_names_read_the_claim_not_the_placeholder(self):
        self.world.run(op("object_delete", CAMERA))
        self.world.run(camera("Camera"))
        # An unnamed duplicate is named after its source, as Actions, slots and separated parts are.
        copy = self.world.run(op("object_duplicate", source="$cam", alias="twin", name=None, linked=False, location=None))
        self.assertEqual(copy["object"], "Camera.001")

    def test_a_name_taken_since_staging_fails_the_commit(self):
        self.world.run(op("object_rename", CAMERA, name="Camera_Old"))
        self.world.run(camera("Camera"))
        FakeObject(self.world.main, "Camera_Old")  # Made by the artist while the stage waited.
        with self.assertRaisesRegex(ProgramError, "NAME_TAKEN") as raised:
            self.world.commit()
        self.assertEqual(raised.exception.code, "STALE_DEPENDENCY")
        self.assertEqual(self.world.names_of(CAMERA), ["Camera"])
        self.assertTrue(self.world.context.aliases["$cam"].name.startswith(names.PLACEHOLDER))

    def test_no_renames_or_claims_touch_nothing(self):
        self.world.run(op("object_delete", CAMERA))
        self.world.live[CAMERA].users_collection = None  # Never read without a rename or a claim.
        self.assertEqual(self.world.commit(), [])




class PendingRenameTargetTests(unittest.TestCase):
    """Regression: a name a pending rename of an existing object will take is taken for later operations.

    The renamed live object keeps its old name in Main until commit, so Main
    shows the new name free. Blender would suffix a new object asking for it,
    and the exact-name kinds refuse at once; before the fix both objects were
    promised the name and commit failed STALE_DEPENDENCY.
    """

    def setUp(self):
        self.world = World(self)
        self.world.add_live(EXTRA, "Camera.001")
        self.world.run(op("object_rename", CAMERA, name="Shot"))

    def test_camera_create_is_suffixed_and_commits(self):
        self.assertEqual(self.world.run(camera("Shot", alias="shot"))["object"], "Shot.001")
        self.world.commit()
        self.assertEqual((self.world.names_of(CAMERA), self.world.context.aliases["$shot"].name), (["Shot"], "Shot.001"))

    def test_exact_name_kinds_refuse_on_the_stage(self):
        for operation in (empty("Shot", "pivot"),
                          op("object_duplicate", source=CUBE, alias="twin", name="Shot", linked=False, location=None),
                          op("object_rename", LAMP, name="Shot")):
            with self.subTest(kind=operation["kind"]), self.assertRaisesRegex(RefusalError, "NAME_TAKEN"):
                self.world.run(operation)
        self.world.run(camera("Take", alias="take"))
        with self.assertRaisesRegex(RefusalError, "NAME_TAKEN"):
            self.world.run(op("object_rename", "$take", name="Shot"))

    def test_an_unnamed_duplicate_skips_a_suffix_a_rename_will_take(self):
        self.world.run(op("object_rename", EXTRA, name="Cube.001"))
        result = self.world.run(op("object_duplicate", source=CUBE, alias="twin", name=None, linked=False, location=None))
        self.assertEqual(result["object"], "Cube.002")  # As Blender: the renamed camera took "Cube.001" first.
        self.world.commit()
        self.assertEqual(self.world.names_of(EXTRA), ["Cube.001"])


class SuffixTests(unittest.TestCase):
    """Suffixes and unnamed copies follow the program's current names, as Blender's would."""

    def setUp(self):
        self.world = World(self)
        self.world.add_live(EXTRA, "Camera.001")

    def test_unnamed_duplicate_of_a_renamed_object_uses_its_new_name(self):
        self.world.run(op("object_rename", CUBE, name="Box"))
        result = self.world.run(op("object_duplicate", source=CUBE, alias="twin", name=None, linked=False, location=None))
        self.assertEqual(result["object"], "Box.001")  # Was Cube.001.

    def test_a_suffix_released_earlier_is_reused(self):
        self.world.run(op("object_delete", EXTRA))  # Releases "Camera.001".
        self.assertEqual(self.world.run(camera("Camera", alias="second"))["object"], "Camera.001")  # Was Camera.002.
        self.world.commit()
        self.assertEqual(self.world.context.aliases["$second"].name, "Camera.001")
        self.assertTrue(self.world.live[EXTRA].name.startswith(names.TEMPORARY))

    def test_program_name_matches_blenders_name_map(self):
        extras = {"live_names": {}}
        held = {"Cube", "Cube.001", "Cube.003"}
        lookup = lambda name: name if name in held else None
        self.assertEqual(names.program_name(extras, "Sphere", lookup), "Sphere")
        self.assertEqual(names.program_name(extras, "Cube", lookup), "Cube.002")
        self.assertEqual(names.program_name(extras, "Cube.003", lookup), "Cube.002")  # The lowest free number.
        self.assertEqual(names.program_name(extras, "Cube.7", lookup), "Cube.7")


class KeptElsewhereTests(unittest.TestCase):
    """Regression: a deleted object another scene links is only unlinked, so it keeps its name."""

    def setUp(self):
        self.world = World(self)
        self.world.share(CAMERA)

    def test_its_name_stays_taken_and_the_commit_succeeds(self):
        self.world.run(op("object_delete", CAMERA))
        self.assertEqual(self.world.run(camera("Camera"))["object"], "Camera.001")  # Was "Camera", then refused.
        self.assertEqual(self.world.commit(), [])
        self.assertEqual((self.world.names_of(CAMERA), self.world.context.aliases["$cam"].name), (["Camera"], "Camera.001"))

    def test_renamed_away_it_releases_its_old_name_and_keeps_the_new_one(self):
        self.world.run(op("object_rename", CAMERA, name="Old"))
        self.world.run(op("object_delete", CAMERA))
        self.assertEqual(self.world.run(camera("Camera"))["object"], "Camera")
        self.assertEqual(self.world.run(camera("Old", alias="second"))["object"], "Old.001")
        self.world.commit()
        self.assertEqual((self.world.names_of(CAMERA), self.world.context.aliases["$cam"].name), (["Old"], "Camera"))

    def test_linked_by_another_scene_only_after_staging_fails_closed(self):
        world = World(self)
        world.run(op("object_delete", CAMERA))
        world.run(camera("Camera"))
        world.live[CAMERA].users_collection.append(FakeCollection())  # Linked while the stage waited.
        before = world.snapshot()
        with self.assertRaisesRegex(ProgramError, "since staging") as raised:
            world.commit()
        self.assertEqual((raised.exception.code, world.snapshot()), ("STALE_DEPENDENCY", before))

    def test_recorded_only_for_objects_the_program_deletes(self):
        world = World(self)
        world.live[CUBE].users_collection.append(FakeCollection())
        names.record_kept_elsewhere(world.context.extras, world.live, [CAMERA, LAMP], [world.scene.collection,
                                                                                      world.collection])
        self.assertNotIn(names.KEPT_ELSEWHERE, world.context.extras)
        names.record_kept_elsewhere(world.context.extras, world.live, [CUBE, "$new"], [world.scene.collection,
                                                                                      world.collection])
        self.assertEqual(world.context.extras[names.KEPT_ELSEWHERE], [CUBE])


class PlaceholderTests(unittest.TestCase):
    """Regression: the private placeholder is never reported or accepted as an expected name."""

    def _backend(self):
        from angelo_agent.program_verification import ProgramVerificationMixin

        class Backend(ProgramVerificationMixin):
            scene = NS(objects=[])

            def _update_stage(self, stage):
                pass

        return Backend()

    def _stage(self):
        created = FakeObject(FakeMain(), "Camera.001", "CAMERA")
        extras = {"live_names": {}}
        names._claim(extras, created, "Camera", "$cam")
        return created, NS(objects={}, originals={}, extras=extras, logical_names={}, changes={},
                           binding={"revisions": {}}, context=NS(aliases={"$cam": created}, scene_handles=()),
                           scene=NS(objects=[created]), view_layer=NS(depsgraph=None))

    def test_stage_name_check_reads_the_claim_and_refuses_the_placeholder(self):
        created, stage = self._stage()
        check = lambda expected: self._backend().validate(stage, {"kind": "object_name", "target": "$cam",
                                                                  "expected": expected})
        self.assertTrue(check("Camera")["passed"])
        self.assertFalse(check("Camera.001")["passed"])
        refused = check(created.name)  # Passed on the stage before the fix, then failed live.
        self.assertFalse(refused["passed"])
        self.assertIn("private staged name", refused["message"])

    def test_a_plan_expecting_a_private_name_is_refused(self):
        from angelo_agent.program_spec import validate_program
        program = {"mode": "APPLY", "operations": [camera("Camera")]}
        self.assertTrue(validate_program(dict(program, expected_checks=[
            {"kind": "object_name", "target": "$cam", "expected": "Camera"}])))
        for private in (names.PLACEHOLDER + "0123456789ab", names.TEMPORARY + "x", "__angelo_copy_ab12cd34_Cube"):
            with self.subTest(name=private), self.assertRaisesRegex(ValueError, "private staged name"):
                validate_program(dict(program, expected_checks=[
                    {"kind": "object_name", "target": "$cam", "expected": private}]))

    def test_later_operation_results_report_the_committed_name(self):
        from angelo_agent.program_objects import ProgramObjectsMixin
        created, stage = self._stage()
        stage.context.extras, stage.changes = stage.extras, {}
        placeholder = created.name

        class Backend(ProgramObjectsMixin):
            def _override(self, stage):
                return contextlib.nullcontext()

        kernels = NS(apply_operation=lambda context, operation: {"kind": operation["kind"], "object": placeholder})
        facts = NS(begin_operation=lambda stage, operation: None, finish_operation=lambda stage, operation, proof: None)
        with patch.dict(sys.modules, {"angelo_agent.action_kernels": kernels,
                                      "angelo_agent.program_material_facts": facts}):
            outcome = Backend().apply(stage, op("camera_create", name="Camera", alias="cam"))
        self.assertEqual(outcome["object"], "Camera")


class SlotNameTests(unittest.TestCase):
    """Regression: the slot a first key creates takes the artist-facing name, never a private staged name.

    Blender names a new slot after the AnimData's last slot identifier, else the
    ID's name (animrig Action::slot_add_for_id), and never renames it. The
    compiled behaviour is test_blender_program_names.py (S6).
    """

    def setUp(self):
        self.world = World(self)

    def test_staged_copy_and_claim_get_their_live_names(self):
        self.world.run(op("object_delete", CAMERA))
        self.world.run(camera("Camera"))
        extras = self.world.context.extras
        for reference, obj, expected in ((CUBE, self.world.context.objects[CUBE], "OBCube"),
                                         ("$cam", self.world.context.aliases["$cam"], "OBCamera")):
            names.name_new_slot(extras, obj, reference)
            self.assertEqual(obj.animation_data.last_slot_identifier, expected)

    def test_an_assigned_or_remembered_slot_and_a_plain_new_object_are_left_alone(self):
        self.world.run(camera("Plain", alias="plain"))
        plain = self.world.context.aliases["$plain"]
        names.name_new_slot(self.world.context.extras, plain, "$plain")
        self.assertIsNone(plain.animation_data)  # Its own name is already the committed one.
        cube = self.world.context.objects[CUBE]
        cube.animation_data_create().last_slot_identifier = "OBHero"
        names.name_new_slot(self.world.context.extras, cube, CUBE)
        self.assertEqual(cube.animation_data.last_slot_identifier, "OBHero")
        lamp = self.world.context.objects[LAMP]
        lamp.animation_data_create().action_slot = object()
        names.name_new_slot(self.world.context.extras, lamp, LAMP)
        self.assertEqual(lamp.animation_data.last_slot_identifier, "")

    def test_every_kernel_that_can_create_a_slot_names_it_before_keying(self):
        root = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        for module, before in (("action_object.py", "obj.keyframe_insert("), ("action_animation.py", "holder.keyframe_insert("),
                               ("anim_copy.py", "animation.action = action")):
            source = (root / module).read_text()
            with self.subTest(module=module):
                self.assertLess(source.index("name_new_slot("), source.index(before))


class ForwardClaimTests(unittest.TestCase):
    """A name every declared holder of which the program releases later is taken at once (program_names.plan_names)."""

    def setUp(self):
        self.world = World(self)

    def commit_names(self):
        self.world.commit()
        return {ref: obj.name for ref, obj in self.world.context.aliases.items()}

    def test_both_orders_commit_the_requested_name(self):
        for order in ((camera("Camera"), op("object_delete", CAMERA)), (op("object_delete", CAMERA), camera("Camera"))):
            with self.subTest(order=[operation["kind"] for operation in order]):
                self.world = World(self)
                self.world.stage(*order)
                self.assertEqual(self.commit_names(), {"$cam": "Camera"})

    def test_a_holder_renamed_away_later(self):
        self.world.stage(camera("Camera"), op("object_rename", CAMERA, name="Camera_Old"))
        self.assertEqual(self.commit_names(), {"$cam": "Camera"})
        self.assertEqual(self.world.names_of(CAMERA), ["Camera_Old"])

    def test_several_holders_and_kinds(self):
        results = self.world.stage(camera("Camera"), empty("Light", "pivot"),
                                   op("object_duplicate", source=LAMP, alias="twin", name="Cube", linked=False, location=None),
                                   op("object_rename", CUBE, name="Box"), op("object_delete", CAMERA),
                                   op("object_rename", LAMP, name="Lamp"))
        self.assertEqual([results[index]["object"] for index in (0, 1, 2)], ["Camera", "Light", "Cube"])
        self.assertEqual(self.commit_names(), {"$cam": "Camera", "$pivot": "Light", "$twin": "Cube"})
        self.assertEqual(self.world.names_of(CUBE, LAMP), ["Box", "Lamp"])

    def test_an_existing_object_takes_a_name_released_later(self):
        self.world.stage(op("object_rename", CUBE, name="Camera"), op("object_delete", CAMERA))  # Was NAME_TAKEN.
        self.world.commit()
        self.assertEqual(self.world.names_of(CUBE), ["Camera"])

    def test_ambiguous_plans_are_refused_before_staging(self):
        cases = {
            "two requests": ([camera("Camera"), camera("Camera", alias="two"), op("object_delete", CAMERA)],
                             "another operation asks for it too"),
            "a later request": ([camera("Camera"), op("object_delete", CAMERA), empty("Camera", "pivot")],
                                "another operation asks for it too"),
            "two holders": ([op("object_rename", CUBE, name="Camera"), camera("Camera"), op("object_delete", CAMERA)],
                            "another operation asks for it too"),
            "renamed back": ([camera("Camera"), op("object_rename", CAMERA, name="Old"),
                              op("object_rename", CAMERA, name="Camera")], "goes back to the existing object 'Camera'"),
        }
        for label, (program, reason) in cases.items():
            with self.subTest(case=label):
                world = World(self)
                with self.assertRaisesRegex(ProgramError, "NAME_AMBIGUOUS: 'Camera' .*" + re.escape(reason)) as raised:
                    world.plan(program)
                self.assertEqual(raised.exception.code, "VALIDATION")
                self.assertNotIn(names.FORWARD, world.context.extras)

    def test_a_deletion_another_scene_survives_does_not_release_it(self):
        self.world.share(CAMERA)
        with self.assertRaisesRegex(ProgramError, "another scene still links it"):
            self.world.plan([camera("Camera"), op("object_delete", CAMERA)])

    def test_without_a_later_release_or_by_an_unnamed_copy_the_sequence_is_blenders(self):
        self.assertEqual(self.world.stage(camera("Camera"))[0]["object"], "Camera.001")
        world = World(self)
        copy, _ = world.stage(op("object_duplicate", source=CAMERA, alias="twin", name=None, linked=False, location=None),
                              op("object_delete", CAMERA))
        self.assertEqual(copy["object"], "Camera.001")  # Nothing asked for "Camera" by name.

    def test_an_unnamed_copy_never_uses_a_later_requests_decision(self):
        # Found by the differential fuzz: the duplicate's alias later asks for "Cube" by name, which the cube's
        # later rename releases; the unnamed duplicate itself is named in Blender's sequence.
        results = self.world.stage(
            op("object_duplicate", source=CUBE, alias="d0", name=None, linked=False, location=None),
            op("object_rename", "$d0", name="Cube"), op("object_rename", CUBE, name="Box"))
        self.assertEqual([results[0]["object"], results[1]["name"]], ["Cube.001", "Cube"])
        self.assertEqual(self.commit_names(), {"$d0": "Cube"})
        self.assertEqual(self.world.names_of(CUBE), ["Box"])

    def test_a_name_held_before_the_program_and_asked_twice_still_fails(self):
        """Break-it E2E-19B M-1: the second creation of a name derives no exact-name check, which is right only when
        the name was free before the program. Whichever creation asks first (unaliased, or a kind without a name check
        of its own), a holder from before the program refuses the program on its stage."""
        for first_alias in (None, "first"):
            with self.subTest(first_alias=first_alias):
                world = World(self)
                FakeObject(world.main, "Held")  # In Main before the program, and not declared.
                with self.assertRaisesRegex(RefusalError, "NAME_TAKEN: an object from before this program is named "
                                                          "'Held'.*would be 'Held.001'"):
                    world.stage(camera("Held", alias=first_alias), camera("Held", alias="second"))
        # A name free before the program, or released by the program first, keeps Blender's suffix and commits.
        world = World(self)
        world.stage(camera("Shot", alias="a"), camera("Shot", alias="b"))
        self.assertEqual([world.context.aliases[alias].name for alias in ("$a", "$b")], ["Shot", "Shot.001"])
        world = World(self)
        world.stage(op("object_delete", CAMERA), camera("Camera", alias="a"), camera("Camera", alias="b"))
        world.commit()
        self.assertEqual(sorted(obj.name for obj in world.context.aliases.values()), ["Camera", "Camera.001"])
        self.assertEqual(names.held_repeats([camera("Held", alias="a"), camera("Held", alias="b"), camera("Free", alias="c"),
                                             camera("Free", alias="d"), camera("Once", alias="e")],
                                            {"Held": object(), "Once": object()}.get), ["Held"])

    def test_a_copy_never_takes_a_name_the_program_asks_for(self):
        """Copies continue their source's numbering, but never into a name another operation of the same program asks
        for: that creation came out 'X.001' and failed its name check (recorded 2026-10-25, phase 1 Sol: copies of
        'Weathered deck plank 0' took 'Weathered deck plank 1', which the program created next)."""
        from angelo_agent import batch_spec

        def create(name, alias):
            return {"kind": "create_primitive", "target": None, "parameters": {"name": name, "alias": alias}}
        operations = [create("Plank 0", "p0"), {"kind": "batch", "target": None, "parameters": {"sources": ["$p0"]}},
                      create("Plank 1", "p1"), {"kind": "object_rename", "target": CUBE, "parameters": {"name": "Plank 5"}}]
        self.assertEqual(names.requested_names(operations), ["Plank 0", "Plank 1", "Plank 5"])
        extras = {names.REQUESTED: names.requested_names(operations)}
        taken = names.copy_name_taken(extras, lambda name: None)
        self.assertEqual(batch_spec.unit_names(["Plank 0"], 4, taken), [(["Plank 2", "Plank 3", "Plank 4"], 1)])
        self.assertEqual(batch_spec.unit_names(["Plank 0"], 6, taken)[0][0], ["Plank 6", "Plank 7", "Plank 8", "Plank 9",
                                                                           "Plank 10"], "a contiguous block past both")
        # Held objects still count, and without requests nothing changes.
        held = names.copy_name_taken({}, lambda name: object() if name == "Plank 1" else None)
        self.assertEqual(batch_spec.unit_names(["Plank 0"], 3, held), [(["Plank 2", "Plank 3"], 1)])
        self.assertEqual(names.created_name(operations[3]), None, "a rename names an existing object")
        self.assertEqual(names.created_name(operations[2]), "Plank 1")

    def test_stage_creation_plans_before_any_staging(self):
        from angelo_agent.program_objects import ProgramObjectsMixin
        collection = FakeCollection()
        world = World(self)
        world.live[CAMERA].users_collection = [collection]

        class Backend(ProgramObjectsMixin):
            scene = NS(collection=FakeCollection(collection))

        binding = {"originals": world.live, "program": {"operations": [camera("Camera"), op("object_delete", CAMERA)]}}
        self.assertEqual(Backend().plan_names(binding), ([], [["$cam", "Camera"]]))
        world.live[CAMERA].users_collection.append(FakeCollection())  # Another scene links it.
        with self.assertRaisesRegex(ProgramError, "NAME_AMBIGUOUS"):
            Backend().plan_names(binding)
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/program_objects.py").read_text()
        self.assertLess(source.index("self.plan_names(binding)"), source.index("bpy.data.scenes.new("))


if __name__ == "__main__":
    unittest.main()
