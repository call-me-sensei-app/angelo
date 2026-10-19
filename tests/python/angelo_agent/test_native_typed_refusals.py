# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed programs the host refused, and should not have, or refused without saying what to change.

Four classes seen in bridge and tree one-shots, where each re-sent program
cost a correction round; each test is written for the class, with paths,
kinds and scenes other than the ones that exposed it:

* a scene setting the allow-list did not count as the scene's (``frame_current``),
  and errors that did not name the operation, the path or the missing field;
* deleting the scene's active camera without setting one: the host clears it
  as part of the delete (``program_refusals.release_camera``), shown for approval;
* an orbit placed after the program's own camera change: measured in program
  order (``program_orbits``), or refused with the exact reordering;
* the Codex-only code-mode sentences (view images, the job tool's callable).

Pure: validators, the identifier table and the controller over host fakes.
The staged, committed and undone forms are in test_blender_object_management.py,
test_blender_camera_light_programs.py and test_blender_property_set.py.
"""

import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import context_ledger, program_camera, program_refusals, rna_rules, scene_binding  # noqa: E402
from angelo_agent.action_camera_light_spec import orbit_location  # noqa: E402
from angelo_agent.action_catalog import validate_operation  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.identifiers import IdentifierTable, expand_call  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.program_orbits import has_orbit, resolve  # noqa: E402
from angelo_agent.program_spec import MAX_OPERATIONS, validate_program  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

EPOCH = "c" * 32
SCENE, CAMERA, LAMP, CRATE, SHELF = ("scn:%s:%d" % (EPOCH, number) for number in (3, 11, 12, 13, 14))
SETTINGS, REVISION = "7" * 64, "8" * 64
STUDIO_CAMERA = {"handle": CAMERA, "location": [0., -9., 3.], "follows": []}


def program(operations, **fields):
    return validate_program(dict({"mode": "APPLY", "operations": copy.deepcopy(operations)}, **fields))


def bound(*handles, write=()):
    """read_set and write_set entries for ``handles`` (``write`` among them in the write set)."""
    return {"read_set": [{"handle": h, "revision": REVISION} for h in handles if h not in write],
            "write_set": [{"handle": h, "revision": SETTINGS if h == SCENE else REVISION} for h in write]}


def observed_table():
    table = IdentifierTable()
    context_ledger.compact_observation({"scene": "Studio", "scene_settings": {"handle": SCENE, "revision": SETTINGS},
                                        "object_count": 3, "objects": [
        {"name": name, "type": kind, "handle": handle, "object_revision": REVISION}
        for name, kind, handle in (("Studio Camera", "CAMERA", CAMERA), ("Lamp", "LIGHT", LAMP),
                                   ("Crate", "MESH", CRATE), ("Shelf", "MESH", SHELF))]}, table)
    return table


def submit(table, *operations):
    """A model's program as the host resolves and validates it (short handles, inferred bindings)."""
    return validate_call("angelo_scene_program", expand_call("angelo_scene_program",
                                                             {"mode": "APPLY", "operations": list(operations)}, table))


def settings(values, target=None):
    operation = {"kind": "property_set", "parameters": {"values": values}}
    return operation if target is None else dict(operation, target=target)


def light(name, orbit=None, alias=None, look_at=(0., 0., 1.)):
    parameters = {"name": name, "look_at": list(look_at), "settings": {"light_type": "AREA", "energy": 400.}}
    parameters.update({"orbit": orbit} if orbit else {"location": [3., 3., 3.]})
    if alias:
        parameters["alias"] = alias
    return {"kind": "light_create", "parameters": parameters}


def camera(name, alias, location, orbit=None):
    parameters = {"name": name, "alias": alias, "look_at": [0., 0., 1.]}
    parameters.update({"orbit": orbit} if orbit else {"location": list(location)})
    return {"kind": "camera_create", "parameters": parameters}


class SceneSettingsAndErrorTests(unittest.TestCase):
    """The scene's own settings are the scene's, and a refusal names the operation, the path and the field."""

    def setUp(self):
        self.table = observed_table()

    def test_the_current_frame_is_a_scene_setting_left_out_of_the_settings_revision(self):
        from angelo_agent.scene_settings import UNREVISIONED_ROOTS
        self.assertEqual(rna_rules.owner_of("frame_current"), rna_rules.SCENE)
        self.assertIn("frame_current", rna_rules.SCENE_ROOTS & UNREVISIONED_ROOTS,
                      "the binding's own frame covers it, so a timeline move stays a frame-only change")

    def test_a_scene_property_set_with_the_frame_binds_the_scene_like_its_other_settings(self):
        for values in ({"frame_current": 48}, {"frame_current": 1, "frame_end": 96, "render.fps": 25},
                       {"camera": "o2", "frame_current": 200, "view_settings.view_transform": "Standard"}):
            with self.subTest(values=values):
                resolved = submit(self.table, settings(values))
                self.assertEqual(resolved["operations"][0]["target"], SCENE)
                self.assertIn({"handle": SCENE, "revision": SETTINGS}, resolved["write_set"])

    def test_without_a_target_an_objects_setting_is_named(self):
        for values, named in (({"hide_render": True}, "'hide_render' is not a scene setting"),
                              ({"frame_end": 60, "data.lens": 50}, "'data.lens' is not a scene setting"),
                              ({"render.fps": 30, "hide_viewport": True, "display_type": "WIRE"},
                               "'hide_viewport' and 'display_type' are not scene settings")):
            for written in (settings(values), dict(settings(values), target=None)):
                with self.subTest(values=values, target="target" in written):
                    for check in (lambda: validate_operation(written), lambda: submit(self.table, written),
                                  lambda: submit(self.table, settings({"frame_start": 1}), written)):
                        with self.assertRaises(ValueError) as caught:
                            check()
                        self.assertIn(named, str(caught.exception))
                        self.assertIn("handle as target", str(caught.exception))

    def test_the_scene_handle_as_target_of_an_objects_setting_is_refused_before_approval(self):
        """It reached staging and was refused there, after approval (OWNER_TYPE_MISMATCH)."""
        for values in ({"hide_select": True}, {"frame_start": 10, "data.energy": 3.}):
            with self.subTest(values=values), self.assertRaises(ValueError) as caught:
                submit(self.table, settings(values, target="o1"))
            self.assertIn("OWNER_TYPE_MISMATCH", str(caught.exception))
            self.assertIn("operations[0] (property_set)", str(caught.exception))

    def test_a_commit_that_moved_the_frame_says_earlier_revisions_no_longer_bind(self):
        from angelo_agent.program_commit_report import FRAME_NOTE, ProgramCommitReportMixin

        class Backend(ProgramCommitReportMixin):
            def __init__(self, frame, live):
                self.frame, self.scene = frame, NS(frame_current=live, frame_subframe=0., world=None)

        stage = NS(extras={}, changes={}, binding={"scene": None})
        with patch.dict(sys.modules, {"angelo_agent.action_world": NS(COMMITTED_NAME="world name")}):  # Needs bpy.
            self.assertEqual(Backend((1, 0.), 60)._commit_notes(stage, [], []), {"frame_moved": FRAME_NOTE})
            self.assertEqual(Backend((60, 0.), 60)._commit_notes(stage, [], []), {})

    def test_mixed_owners_name_the_object_settings(self):
        with self.assertRaises(rna_rules.PropertyPathError) as caught:
            validate_operation(settings({"render.fps": 24, "hide_render": True, "data.lens": 35}, target=LAMP))
        self.assertEqual(caught.exception.code, "MIXED_OWNERS")
        self.assertIn("not scene settings: hide_render, data.lens", str(caught.exception))

    def test_a_missing_field_is_named_with_its_operation(self):
        operations = [{"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Box", "alias": "box"}},
                      {"kind": "transform", "parameters": {"location": [0, 0, 1]}},
                      {"kind": "object_rename", "target": "$box", "parameters": {}}]
        for index in (1, 2):
            body = operations[:1] + operations[index:index + 1]
            with self.subTest(kind=body[1]["kind"]):
                for check in (lambda: program(body), lambda: submit(self.table, *copy.deepcopy(body))):
                    with self.assertRaises(ValueError) as caught:
                        check()
                    text = str(caught.exception)
                    self.assertTrue(text.startswith("operations[1] (%s): " % body[1]["kind"]), text)
                    self.assertIn("missing required fields: " + ("target" if index == 1 else "name"), text)

    def test_a_refusal_keeps_its_code_and_names_its_operation(self):
        sky = {"kind": "world_configure", "parameters": {"environment": "dat:%s:IMAGE:7" % EPOCH, "strength": 1.,
                                                         "sky": {"type": "MULTIPLE_SCATTERING"}}}
        from angelo_agent.refusal_codes import refusal_code
        with self.assertRaises(ValueError) as caught:
            submit(self.table, settings({"frame_end": 10}), sky)
        self.assertEqual(refusal_code(caught.exception), "VALIDATION")
        self.assertTrue(str(caught.exception).startswith("operations[1] (world_configure): world_configure "))

    def test_a_creating_operation_given_a_target_is_told_to_leave_it_out(self):
        """Was "'o1' is not an object handle" for the scene's own handle."""
        for operation in (camera("Shot", "shot", (1, 1, 1)), light("Fill"),
                          {"kind": "create_primitive", "parameters": {"primitive": "PLANE"}}):
            for target in ("o1", "o4"):
                with self.subTest(kind=operation["kind"], target=target), self.assertRaises(ValueError) as caught:
                    submit(self.table, dict(operation, target=target))
                self.assertIn("creates its object, so it takes no target", str(caught.exception))

    def test_an_unknown_kind_is_not_echoed(self):
        with self.assertRaises(ValueError) as caught:
            program([{"kind": "x" * 500, "parameters": {}}])
        self.assertTrue(str(caught.exception).startswith("operations[0]: "))
        self.assertNotIn("xxxx", str(caught.exception))


class ActiveCameraReleaseTests(unittest.TestCase):
    """Deleting the scene's camera clears it, in the approved program; what the rule protects stays refused."""

    def delete(self, *extra, targets=(CAMERA,), scene_declared=None):
        operations = [{"kind": "object_delete", "target": target, "parameters": {}} for target in targets] + list(extra)
        handles = set(targets) | {reference for operation in extra for reference in (operation.get("target"),)
                                  if isinstance(reference, str) and reference.startswith("scn:")}
        writes = set(targets) | ({SCENE} if scene_declared == "write" else set())
        if scene_declared == "read":
            handles.add(SCENE)
        return program(operations, **bound(*sorted(handles | writes), write=writes))

    def test_a_delete_without_a_camera_of_its_own_clears_it_last(self):
        for targets in ((CAMERA,), (CRATE, CAMERA, LAMP)):
            with self.subTest(targets=len(targets)):
                submitted = self.delete(targets=targets)
                released = program_refusals.release_camera(submitted, SCENE, SETTINGS, CAMERA)
                self.assertEqual(released["operations"][:-1], submitted["operations"], "The model's own come first")
                self.assertEqual(released["operations"][-1], {"kind": "property_set", "target": SCENE,
                                                              "parameters": {"values": {"camera": None}}})
                self.assertIn({"handle": SCENE, "revision": SETTINGS}, released["write_set"])
                self.assertEqual(released, validate_program(released), "Canonical")
                self.assertIn({"kind": "property", "target": SCENE, "values": {"camera": None}},
                              __import__("angelo_agent.program_checks", fromlist=["required_checks"]).required_checks(released))

    def test_a_program_that_sets_the_camera_anywhere_is_left_as_written(self):
        for where in ("before", "after"):
            for value in (None, LAMP):  # None clears it; any other object (the kernel checks it is a camera).
                with self.subTest(where=where, value=value):
                    change = settings({"camera": value}, target=SCENE)
                    operations = [change] if where == "before" else []
                    submitted = program(operations + [{"kind": "object_delete", "target": CAMERA, "parameters": {}}]
                                        + ([change] if where == "after" else []),
                                        **bound(SCENE, CAMERA, *([LAMP] if value else []), write={SCENE, CAMERA}))
                    self.assertFalse(program_refusals.releases(submitted, CAMERA))
                    self.assertIs(program_refusals.release_camera(submitted, SCENE, SETTINGS, CAMERA), submitted)

    def test_the_scenes_declared_revision_is_kept(self):
        for declared in ("read", "write"):
            with self.subTest(declared=declared):
                released = program_refusals.release_camera(self.delete(scene_declared=declared), SCENE, "9" * 64, CAMERA)
                self.assertEqual([entry for entry in released["write_set"] if entry["handle"] == SCENE],
                                 [{"handle": SCENE, "revision": SETTINGS if declared == "write" else REVISION}])
                self.assertNotIn(SCENE, [entry["handle"] for entry in released["read_set"]])

    def test_another_object_or_no_room_is_left_to_binding(self):
        other = self.delete(targets=(CRATE,))
        self.assertIs(program_refusals.release_camera(other, SCENE, SETTINGS, CAMERA), other)
        filler = [{"kind": "object_rename", "target": SHELF, "parameters": {"name": "Shelf %d" % index}}
                  for index in range(MAX_OPERATIONS - 1)]
        full = program([{"kind": "object_delete", "target": CAMERA, "parameters": {}}] + filler,
                       **bound(CAMERA, SHELF, write={CAMERA, SHELF}))
        self.assertIs(program_refusals.release_camera(full, SCENE, SETTINGS, CAMERA), full)

    @staticmethod
    def blender(scenes, holders=()):
        """A stand-in ``bpy``: ``holders`` point at every object user_map is asked about (looked up by identity)."""
        class Users:
            def __init__(self, subset):
                self.subset = list(subset)

            def get(self, obj, default=()):
                return list(holders) if any(item is obj for item in self.subset) else default
        return NS(data=NS(scenes=scenes, user_map=lambda subset: Users(subset)),
                  types=NS(Object=type("Object", (), {}), Collection=type("Collection", (), {}),
                           Scene=type("Scene", (), {})))

    def check_deletions(self, submitted, scenes, holders=()):
        originals = {CAMERA: scenes[0].camera, CRATE: NS(name="Crate")}
        with patch.dict(sys.modules, {"bpy": self.blender(scenes, holders)}):
            program_refusals.check_deletions(scenes[0], SCENE, originals, submitted)

    def test_a_fake_users_datablock_pointing_at_a_deleted_object_is_refused_at_binding(self):
        """Commit's in-use check refused it after approval, and no kind changes a kept datablock's pointer; data the
        program may still change (no fake user) is left to commit (earlier, never more)."""
        scenes = [NS(camera=None, name="Studio")]
        crate = program([{"kind": "object_delete", "target": CRATE, "parameters": {}}], **bound(CRATE, write={CRATE}))
        kept = NS(use_fake_user=True, name="Pointer", bl_rna=NS(name="Material"))
        with self.assertRaisesRegex(ProgramError, "OBJECT_IN_USE: Material 'Pointer', kept by its fake user"):
            self.check_deletions(crate, scenes, holders=[kept])
        self.check_deletions(crate, scenes, holders=[NS(use_fake_user=False, name="Used", bl_rna=NS(name="Material"))])

    def test_binding_admits_the_released_program_and_keeps_its_refusals(self):
        live = NS(name="Studio Camera")
        bound_scene, other_scene = NS(camera=live, name="Studio"), NS(camera=None, name="Layout")
        released = program_refusals.release_camera(self.delete(), SCENE, SETTINGS, CAMERA)
        self.check_deletions(released, [bound_scene, other_scene])
        with self.assertRaisesRegex(ProgramError, "ACTIVE_CAMERA: 'Studio Camera' is the scene's active camera"):
            self.check_deletions(self.delete(), [bound_scene, other_scene])  # Bound without the host's completion.
        other_scene.camera = live
        with self.assertRaisesRegex(ProgramError, "active camera of the scene 'Layout'"):
            self.check_deletions(released, [bound_scene, other_scene])  # This program cannot release that scene's.
        itself = program([{"kind": "object_delete", "target": CAMERA, "parameters": {}},
                          settings({"camera": CAMERA}, target=SCENE)], **bound(SCENE, CAMERA, write={SCENE, CAMERA}))
        self.assertIs(program_refusals.release_camera(itself, SCENE, SETTINGS, CAMERA), itself)
        with self.assertRaisesRegex(ProgramError, "ACTIVE_CAMERA"):
            self.check_deletions(itself, [bound_scene, NS(camera=None, name="Layout")])

    def test_host_program_reads_the_scene_only_for_a_deleted_camera(self):
        window = NS(scene=NS(camera=NS(name="Studio Camera")))
        reads = []
        with patch.dict(sys.modules, {"angelo_agent.scene_queries": NS(object_handle=lambda obj: CAMERA)}), \
                patch("angelo_agent.scene_settings.scene_handle", lambda scene: reads.append("handle") or SCENE), \
                patch("angelo_agent.scene_settings.settings_revision", lambda scene: reads.append("revision") or SETTINGS):
            untouched = self.delete(targets=(CRATE,))
            self.assertIs(program_camera.host_program(window, untouched), untouched)
            self.assertEqual(reads, [])
            released = program_camera.host_program(window, self.delete())
        self.assertEqual(reads, ["handle", "revision"])
        self.assertEqual(released["operations"][-1]["parameters"]["values"], {"camera": None})


class ReleasingExecutor:
    """The live scene's camera is CAMERA; resolves and releases like BlenderExecutor.host_program."""

    def __init__(self, camera=STUDIO_CAMERA):
        self.reads, self.preconditions, self.started, self.window, self.camera = 0, [], [], "window", camera

    def supports_native_job(self, name):
        return name in {"angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"}

    def host_program(self, arguments, used=None):
        self.reads += 1
        arguments = resolve(arguments, self.camera, used=used)
        return (program_refusals.release_camera(arguments, SCENE, SETTINGS, CAMERA)
                if program_refusals.releases(arguments, CAMERA) else arguments)

    def precondition(self, name, arguments):
        self.preconditions.append(copy.deepcopy(arguments))
        return ("bound", json.dumps(arguments, sort_keys=True))

    def start_native_job(self, name, arguments):
        self.started.append((name, copy.deepcopy(arguments)))
        raise RuntimeError("fixture stops at staging")


class ControllerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("instance", "scratch.blend", "Scene"))
        self.c.busy = True

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def request(self, executor, arguments, identity="clear"):
        answers = []
        self.c.request_tool(identity, "angelo_scene_program", copy.deepcopy(arguments), answers.append)
        for _tick in range(4):
            self.c.pump_tools(executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
            if answers or executor.started or executor.preconditions:
                break
        return self.c.pending.get(self.c.state["id"] + "/" + identity), answers

    def test_the_release_is_fixed_once_shown_for_approval_and_staged(self):
        from angelo_agent.approvals import review_arguments
        executor = ReleasingExecutor()
        arguments = {"mode": "APPLY", "operations": [{"kind": "object_delete", "target": target, "parameters": {}}
                                                     for target in (CRATE, CAMERA)], **bound(CRATE, CAMERA, write={CRATE, CAMERA})}
        call, answers = self.request(executor, arguments)
        self.assertTrue(call.approval and not answers)
        self.assertEqual(executor.preconditions, [call.program_arguments])
        self.assertEqual(review_arguments(call)["operations"][-1]["parameters"]["values"], {"camera": None})
        self.assertEqual(len(call.arguments["operations"]), 2, "The submitted call keeps what the model wrote")
        self.c.approve_tool(call, executor, self.c.document)
        self.c.pump_tools(executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(executor.started, [("angelo_scene_program", call.program_arguments)])
        self.assertEqual(executor.reads, 1, "Later preconditions bind the fixed program and never read again")

    def test_a_program_the_camera_does_not_change_is_bound_as_written(self):
        executor = ReleasingExecutor()
        arguments = {"mode": "APPLY", "operations": [{"kind": "object_delete", "target": CRATE, "parameters": {}}],
                     **bound(CRATE, write={CRATE})}
        call, _answers = self.request(executor, arguments)
        self.assertIsNone(call.program_arguments)
        self.assertEqual(executor.reads, 1)
        self.assertEqual(executor.preconditions, [validate_program(copy.deepcopy(arguments))])

    def test_an_executor_that_cannot_read_the_camera_leaves_binding_to_refuse(self):
        executor = ReleasingExecutor()
        executor.host_program = None
        arguments = {"mode": "APPLY", "operations": [{"kind": "object_delete", "target": CAMERA, "parameters": {}}],
                     **bound(CAMERA, write={CAMERA})}
        call, _answers = self.request(executor, arguments)
        self.assertIsNone(call.program_arguments)
        self.assertEqual(len(executor.preconditions[0]["operations"]), 1)


class FinalCameraOrbitTests(unittest.TestCase):
    """An orbit is measured from the camera the scene has once the program has run, wherever it is set."""

    KEY = {"azimuth": -math.pi / 4, "elevation": .5}

    def locations(self, operations, cameras=None, camera=STUDIO_CAMERA, **binding):
        result = resolve(program(operations, **binding), camera, cameras)
        self.assertFalse(has_orbit(result))
        return [operation["parameters"]["location"] for operation in result["operations"]
                if operation["kind"] == "light_create"]

    def refusal(self, operations, cameras=None, **binding):
        with self.assertRaises(RefusalError) as caught:
            resolve(program(operations, **binding), STUDIO_CAMERA, cameras)
        self.assertEqual(caught.exception.code, "VALIDATION")
        return str(caught.exception)

    def around(self, where, orbit=None):
        return orbit_location(list(where), [0., 0., 1.], orbit or self.KEY)

    def test_the_programs_own_camera_in_any_order(self):
        """The four recorded tree shapes: the camera set before the orbit, after it, after clearing or deleting the old."""
        made, sets = camera("Overhead", "overhead", (4., 6., 2.)), settings({"camera": "$overhead"}, target=SCENE)
        clear, delete = settings({"camera": None}, target=SCENE), {"kind": "object_delete", "target": CAMERA, "parameters": {}}
        for operations in ([made, sets, light("Key", self.KEY)], [made, light("Key", self.KEY), sets],
                           [light("Key", self.KEY), made, sets], [clear, delete, made, light("Key", self.KEY), sets],
                           [delete, made, light("Key", self.KEY), sets], [made, sets, delete, light("Key", self.KEY)]):
            with self.subTest(kinds=[operation["kind"] for operation in operations]):
                self.assertEqual(self.locations(operations, **bound(SCENE, CAMERA, write={SCENE, CAMERA})),
                                 [self.around((4., 6., 2.))])

    def test_a_scene_without_a_camera_resolves_once_the_program_makes_one(self):
        operations = [light("Key", self.KEY), camera("Overhead", "overhead", (4., 6., 2.)),
                      settings({"camera": "$overhead"}, target=SCENE)]
        self.assertEqual(self.locations(operations, camera=None, **bound(SCENE, write={SCENE})), [self.around((4., 6., 2.))])
        with self.assertRaisesRegex(RefusalError, "has none"):
            resolve(program([light("Key", self.KEY)]), None)

    def test_an_existing_camera_made_the_scenes_is_read_by_the_host(self):
        shelf = {"handle": SHELF, "location": [-5., 2., 4.], "follows": [CRATE]}
        operations = [settings({"camera": SHELF}, target=SCENE), light("Key", self.KEY)]
        self.assertEqual(self.locations(operations, {SHELF: shelf}, **bound(SCENE, SHELF, write={SCENE})),
                         [self.around((-5., 2., 4.))])
        self.assertIn("states no position", self.refusal(operations, **bound(SCENE, SHELF, write={SCENE})))
        moved = operations + [{"kind": "transform", "target": CRATE, "parameters": {"location": [0., 0., 9.]}}]
        self.assertIn("operations[2] (transform) moves an object the scene camera follows",
                      self.refusal(moved, {SHELF: shelf}, **bound(SCENE, SHELF, CRATE, write={SCENE, CRATE})))

    def test_a_program_that_leaves_no_camera_measures_from_the_camera_at_the_orbit(self):
        operations = [light("Key", self.KEY), {"kind": "object_delete", "target": CAMERA, "parameters": {}}]
        self.assertEqual(self.locations(operations, **bound(CAMERA, write={CAMERA})), [self.around(STUDIO_CAMERA["location"])])

    def test_a_camera_whose_position_the_program_decides_is_refused(self):
        made, sets = camera("Overhead", "overhead", (4., 6., 2.)), settings({"camera": "$overhead"}, target=SCENE)
        move = {"kind": "transform", "target": "$overhead", "parameters": {"location": [0., 0., 9.]}}
        copies = {"kind": "batch", "parameters": {"sources": ["$overhead"], "at": [[4., 6., 2.], [8., 6., 2.]]}}
        cases = {
            "edited before": ([made, move, sets, light("Key", self.KEY)], "operations[1] (transform) edits the scene camera"),
            "edited after": ([made, sets, light("Key", self.KEY), move], "operations[3] (transform) edits the scene camera"),
            "a batch copy": ([made, copies, settings({"camera": "$overhead_2"}, target=SCENE), light("Key", self.KEY)],
                             "states no position"),
            "each other's": ([camera("A", "a", None, orbit=self.KEY), settings({"camera": "$a"}, target=SCENE),
                              camera("B", "b", None, orbit=self.KEY), settings({"camera": "$b"}, target=SCENE)],
                             "depend on each other"),
        }
        for name, (operations, words) in cases.items():
            with self.subTest(case=name):
                self.assertIn(words, self.refusal(operations, **bound(SCENE, write={SCENE})))

    def test_a_camera_placed_by_orbit_may_become_the_scenes(self):
        """It is measured from the camera it replaces, which may then go; later orbits from it."""
        shot = {"azimuth": math.pi / 3, "elevation": .2}
        rim = {"azimuth": math.pi, "elevation": .6}
        operations = [camera("Side Shot", "side", None, orbit=shot), settings({"camera": "$side"}, target=SCENE),
                      {"kind": "object_delete", "target": CAMERA, "parameters": {}}, light("Rim", rim)]
        result = resolve(program(operations, **bound(SCENE, CAMERA, write={SCENE, CAMERA})), STUDIO_CAMERA)
        side = self.around(STUDIO_CAMERA["location"], shot)
        self.assertEqual(result["operations"][0]["parameters"]["location"], side)
        self.assertEqual(result["operations"][3]["parameters"]["location"], self.around(side, rim))

    def test_programs_that_leave_the_camera_alone_resolve_as_before(self):
        operations = [light("Key", self.KEY), light("Fill", {"azimuth": 1.1, "elevation": .3}),
                      {"kind": "object_delete", "target": CRATE, "parameters": {}}]
        self.assertEqual(self.locations(operations, **bound(CRATE, write={CRATE})),
                         [self.around(STUDIO_CAMERA["location"], orbit) for orbit in (self.KEY, {"azimuth": 1.1, "elevation": .3})])


class ColourBesideASkyTests(unittest.TestCase):
    """A colour beside the background's own source is kept and reported unused, not refused."""

    def test_admitted_with_both_sources_still_refused(self):
        table = observed_table()
        sky = {"type": "MULTIPLE_SCATTERING", "sun_elevation": .1}
        for parameters in ({"color": [.9, .5, .2], "sky": sky}, {"color": [.9, .5, .2], "environment": "dat:%s:IMAGE:7" % EPOCH}):
            with self.subTest(parameters=sorted(parameters)):
                resolved = submit(table, {"kind": "world_configure", "parameters": parameters})
                self.assertEqual(resolved["operations"][0]["parameters"]["color"], [.9, .5, .2])
        with self.assertRaisesRegex(ValueError, "ONE_BACKGROUND"):
            submit(table, {"kind": "world_configure", "parameters": {"sky": sky, "environment": "dat:%s:IMAGE:7" % EPOCH}})

    def test_the_kernel_reports_the_colour_unused(self):
        from angelo_agent.action_world_spec import COLOR_UNUSED  # action_world.apply_world_configure's note.
        note = COLOR_UNUSED % ("sky", "sky")
        self.assertIn("the sky drives the rendered background", note)
        self.assertIn("still the World colour Solid and Workbench views show", note)


class BreakItReviewTests(unittest.TestCase):
    """Reproductions of edge cases these fixes missed at first, each asserting the fixed behaviour."""

    KEY = {"azimuth": math.pi, "elevation": .4}

    def test_a_copy_reaching_its_root_only_through_also_is_refused(self):
        """Was admitted: commit points the copy at the root's data, whose faces kept the old material."""
        root, other = "scn:%s:21" % EPOCH, "scn:%s:22" % EPOCH
        dup = {"kind": "object_duplicate", "parameters": {"source": root, "alias": "copy", "linked": True}}
        paint = {"kind": "material_principled", "target": "$copy", "parameters": {
            "name": "Paint", "inputs": {"Base Color": [.8, .1, .1, 1]}, "also": [root]}}
        brass = {"kind": "material_assign", "target": "$copy", "parameters": {"material": "Brass", "source": other,
                                                                             "also": [root]}}
        chain = [dup, {"kind": "object_duplicate", "parameters": {"source": "$copy", "alias": "copy2", "linked": True}},
                 dict(paint, target="$copy2", parameters=dict(paint["parameters"], also=[root, "$copy"]))]
        for operations in ([dup, paint], [dup, brass], chain):
            with self.subTest(kinds=[operation["kind"] for operation in operations]):
                with self.assertRaisesRegex(ValueError, "LINKED_EDIT"):
                    program(operations, read_set=[{"handle": other, "revision": REVISION}],
                            write_set=[{"handle": root, "revision": REVISION}])

    def test_an_orbit_beside_a_frame_move_needs_a_camera_that_keeps_still(self):
        moving = dict(STUDIO_CAMERA, moves_with_time=True)
        frame = settings({"frame_current": 120}, target=SCENE)
        for operations in ([frame, light("Rim", self.KEY)], [light("Rim", self.KEY), frame]):
            with self.subTest(first=operations[0]["kind"]):
                arguments = program(operations, **bound(SCENE, write={SCENE}))
                with self.assertRaisesRegex(RefusalError, "writes frame_current, after which Blender evaluates the scene at its frame again, and that camera moves with time"):
                    resolve(arguments, moving)
                self.assertFalse(has_orbit(resolve(arguments, STUDIO_CAMERA)), "A camera that keeps still resolves")
        made = [camera("Shot", "shot", (8., 8., 4.)), settings({"camera": "$shot"}, target=SCENE), frame,
                light("Rim", self.KEY)]
        self.assertFalse(has_orbit(resolve(program(made, **bound(SCENE, write={SCENE})), moving)),
                         "A camera the program makes does not move with time")

    def test_a_frame_rebind_never_moves_an_orbit_measured_from_a_moving_camera(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        controller = Controller(directory.name)
        self.addCleanup(controller.close)
        controller.set_document(("instance", "scratch.blend", "Scene"))
        controller.busy = True
        for moves in (False, True):
            with self.subTest(moves=moves):
                executor = ReleasingExecutor(camera=dict(STUDIO_CAMERA, moves_with_time=moves))
                executor.rebind_program = lambda *arguments: {"refused": "rebound", "error_code": "FRAME_CHANGED"}
                answers = []
                identity = "orbit-%s" % moves
                controller.request_tool(identity, "angelo_scene_program",
                                        {"mode": "APPLY", "operations": [light("Rim", self.KEY)]}, answers.append)
                for _tick in range(4):
                    controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
                    if answers or executor.preconditions:
                        break
                call = controller.pending[controller.state["id"] + "/" + identity]
                self.assertIs(call.orbit_moves_with_time, moves)
                outcome = controller._frame_rebind(call, executor)
                self.assertEqual(("moves with time" in outcome["refused"]), moves)

    def test_a_frame_move_is_refused_beside_camera_markers_and_animation(self):
        curves = NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=[NS(data_path="location")])])])])
        animated = NS(name="Car", animation_data=NS(action=curves, drivers=[], nla_tracks=[]), data=None)
        still = NS(name="Shelf", animation_data=None, data=None)
        frame = settings({"frame_current": 60}, target=SCENE)
        plain = NS(timeline_markers=[NS(name="Start", camera=None)])
        cases = (
            (NS(timeline_markers=[NS(name="Cut A", camera=NS(name="Cam2"))]), [frame], {}, "FRAME_CAMERA_MARKERS: the markers 'Cut A'"),
            (plain, [{"kind": "marker_edit", "target": SCENE, "parameters": {
                "operation": "ADD", "name": "Cut", "frame": 60, "camera": CAMERA}}, frame], {}, "this program's markers"),
            (plain, [frame, {"kind": "transform", "target": CRATE, "parameters": {"location": [0, 0, 1]}}],
             {CRATE: animated}, "FRAME_ANIMATED: operations[1] (transform) sets location of 'Car' without a key"),
            )
        for scene, operations, originals, words in cases:
            with self.subTest(words=words):
                handles = sorted({operation.get("target") for operation in operations
                                  if str(operation.get("target")).startswith("scn:")} | {SCENE})
                arguments = program(operations, **bound(CAMERA, *handles, write=set(handles)))
                with self.assertRaises(ProgramError) as caught:
                    program_refusals.check_frame_move(scene, originals, arguments)
                self.assertIn(words, str(caught.exception))
                self.assertEqual(caught.exception.code, "VALIDATION")
        allowed = program([frame, {"kind": "transform", "target": SHELF, "parameters": {"location": [0, 0, 1]}}],
                          **bound(SCENE, SHELF, write={SCENE, SHELF}))
        program_refusals.check_frame_move(plain, {SHELF: still}, allowed)
        # A recorded bridge shape: a car made and keyed here, and a frame of its drive; its checks sample the keys' frames.
        keyed = program([{"kind": "create_object", "parameters": {"type": "EMPTY", "alias": "car", "location": [0, -1, 0]}},
                         {"kind": "keyframe_insert", "target": "$car", "parameters": {
                             "property": "location", "value": [-10.5, -1, 0], "frame": 1}},
                         {"kind": "keyframe_insert", "target": "$car", "parameters": {
                             "property": "location", "value": [10.5, -1, 0], "frame": 120}}, frame],
                        **bound(SCENE, write={SCENE}))
        program_refusals.check_frame_move(plain, {}, keyed)
        program_refusals.check_frame_move(NS(timeline_markers=[NS(name="Cut", camera=object())]), {},
                                          program([settings({"frame_end": 9}, target=SCENE)], **bound(SCENE, write={SCENE})))

    def test_malformed_parameters_stay_validation_refusals(self):
        """Was AttributeError (scene_binding._object_paths), which ended a Codex call and a Claude turn."""
        table = observed_table()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        controller = Controller(directory.name)
        self.addCleanup(controller.close)
        controller.set_document(("instance", "scratch.blend", "Scene"))
        controller.busy = True
        from angelo_agent.action_schemas import OPERATION_SCHEMAS
        for kind in sorted(OPERATION_SCHEMAS):
            for parameters in (["x"], "values", None, {}, {"values": ["hide_render"]}, {"values": "x"},
                               {"values": {1: True}}, {"values": {"frame_current": "x", "hide_render": True}}):
                for target in ("<none>", None, "o1", "o2", "$a", ""):
                    operation = {"kind": kind, "parameters": parameters}
                    if target != "<none>":
                        operation["target"] = target
                    for check in (lambda: validate_operation(copy.deepcopy(operation)),
                                  lambda: submit(table, copy.deepcopy(operation))):
                        try:
                            check()
                        except ValueError:
                            pass
                        except Exception as error:  # noqa: BLE001
                            self.fail("%s %r %r: %s %s" % (kind, parameters, target, type(error).__name__, error))
        for parameters in (["x"], "values"):
            answers = []
            controller.request_tool("bad-%s" % type(parameters).__name__, "angelo_scene_program",
                                    {"mode": "APPLY", "operations": [{"kind": "property_set", "parameters": parameters}]},
                                    answers.append)
            self.assertEqual(answers[0]["error_code"], "VALIDATION")
            self.assertIn("operations[0] (property_set)", answers[0]["error"])

    def test_the_host_added_camera_clear_is_labelled_the_scene(self):
        from angelo_agent import agent_target_labels
        submitted = program([{"kind": "object_delete", "target": CAMERA, "parameters": {}}],
                            **bound(CAMERA, write={CAMERA}))
        released = program_refusals.release_camera(submitted, SCENE, SETTINGS, CAMERA)
        scene = NS(session_uid=3, name="Studio", objects=[NS(session_uid=11, name="Studio Camera")])
        call = NS(name="angelo_scene_program", id="clear", arguments=submitted, program_arguments=None, precondition=None)
        controller = NS(document="doc", document_invalid=False, pending={"clear": call}, state={"id": "chat"})
        handles = {id(scene): SCENE, id(scene.objects[0]): CAMERA}
        import angelo_agent
        stub = NS(object_handle=lambda obj: handles[id(obj)])  # prepare imports it as the package's attribute.
        with patch.dict(sys.modules, {"angelo_agent.scene_queries": stub}), \
                patch.object(angelo_agent, "scene_queries", stub, create=True):
            agent_target_labels.prepare(controller, NS(scene=scene), "doc")
            self.assertFalse(getattr(call, "_target_labels_prepared", False), "Not before the host fixed the program")
            call.program_arguments, call.precondition = released, ("bound",)
            agent_target_labels.prepare(controller, NS(scene=scene), "doc")
        self.assertEqual(call.target_labels, {SCENE: "Scene 'Studio' settings", CAMERA: "Studio Camera"})


class CodexGuidanceTests(unittest.TestCase):
    """Two code-mode sentences only Codex threads read; Claude's instructions are unchanged by them."""

    def test_codex_code_mode_shows_the_view_loop_and_the_job_callable(self):
        from angelo_agent import providers
        text = providers.codex_instructions(short_identifiers=True, deferred=True, plan=True)
        flat = " ".join(text.split())   # Facts-only text: same facts, wrapped differently.
        self.assertIn("image(url) shows you (its second argument, if given, is a detail string)", flat)
        self.assertIn("angelo_job_start needs no lookup: call tools.more__angelo_job_start", flat)
        for claude in (providers.CLAUDE_INSTRUCTIONS, providers.INSTRUCTIONS):
            self.assertNotIn("detail string", claude)
            self.assertNotIn("more__angelo_job_start", claude)


if __name__ == "__main__":
    unittest.main()
