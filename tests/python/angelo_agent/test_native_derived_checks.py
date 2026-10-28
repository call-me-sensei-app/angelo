# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checks the host derives from a program's own operations (toolset 18).

Pure validation, the staged job with a fake backend, and the host summary:
no Blender, provider or inference.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import derived_checks  # noqa: E402
from angelo_agent.action_schemas import OPERATION_SCHEMAS  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion  # noqa: E402
from angelo_agent.program_jobs import PreviewStore, ProgramJob, WriterLease  # noqa: E402
from angelo_agent.program_checks import required_checks, resolve_obligations
from angelo_agent.program_spec import MAX_CHECKS, validate_program  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, validate_call  # noqa: E402
from angelo_agent.validation_reader import values_near  # noqa: E402
from angelo_agent.validation_spec import validate_check, validate_checks  # noqa: E402
from test_native_program_jobs import Backend, finish  # noqa: E402
from test_native_complete_checks import material_binding_facts  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":1"
OTHER = "scn:" + "a" * 32 + ":2"
REVISION = "a" * 64


def call(operations, checks=None, *, handles=(HANDLE,), mode="APPLY"):
    binding = [{"handle": handle, "revision": REVISION} for handle in handles]
    arguments = {"mode": mode, "read_set": [], "write_set": binding, "operations": operations}
    if checks is not None:
        arguments["expected_checks"] = checks
    return validate_call("angelo_scene_program", arguments)


def cube(**parameters):
    return {"kind": "create_primitive", "target": None,
            "parameters": dict({"primitive": "CUBE", "alias": "box"}, **parameters)}


def kinds(checks):
    return [(check["kind"], check.get("property") or check.get("name") or check.get("material")) for check in checks]


class DerivationTests(unittest.TestCase):
    def test_a_named_cube_states_its_name_location_and_size(self):
        checks = required_checks(call([cube(name="Eval Cube", size=2, location=[0, 0, 0])], [], handles=()))
        self.assertEqual(checks, [
            {"kind": "object_name", "target": "$box", "expected": "Eval Cube"},
            {"kind": "transform", "target": "$box", "property": "location", "expected": [0.0, 0.0, 0.0]},
            {"kind": "dimensions", "target": "$box", "expected": [2.0, 2.0, 2.0], "space": "WORLD"}])

    def test_defaults_and_unprovable_parameters_derive_nothing_extra(self):
        # The default name may collide (".001"); identity rotation/scale prove nothing useful.
        checks = required_checks(call([cube()], handles=()))
        self.assertEqual(kinds(checks), [("transform", "location"), ("dimensions", None)])
        rotated = required_checks(call([cube(name="Tilted", rotation=[0, 0, 1], scale=[2, 1, 1])], handles=()))
        self.assertEqual(kinds(rotated), [("object_name", None), ("transform", "location"),
                                          ("transform", "rotation_euler"), ("transform", "scale")])
        sphere = required_checks(call([{"kind": "create_primitive", "target": None,
                        "parameters": {"primitive": "UV_SPHERE", "alias": "ball"}}], handles=()))
        self.assertNotIn("dimensions", [check["kind"] for check in sphere])
        unnamed = call([{"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE"}}], handles=())
        self.assertEqual(required_checks(unnamed), [], "Without an alias nothing can be checked")

    def test_a_name_asked_twice_in_one_program_is_checked_once(self):
        """Blender keeps object names unique, so the second creation of a name this program already asked for is
        named its next free ".NNN" (the result says which). Its exact-name check rolled the whole program back
        (recorded 2026-10-25, phase 1 workers: Opus 'Dock_Stringer' twice, Sol a pin twice and a stem three times)."""
        first, second = cube(name="Pin", location=[0, 0, 0]), cube(name="Pin", alias="pin2", location=[1, 0, 0])
        checks = required_checks(call([first, second], handles=()))
        self.assertEqual([check for check in checks if check["kind"] == "object_name"],
                         [{"kind": "object_name", "target": "$box", "expected": "Pin"}])
        self.assertIn({"kind": "transform", "target": "$pin2", "property": "location", "expected": [1.0, 0.0, 0.0]},
                      checks, "only the name check goes")
        distinct = required_checks(call([first, cube(name="Pin 2", alias="pin2", location=[1, 0, 0])], handles=()))
        self.assertEqual(len([check for check in distinct if check["kind"] == "object_name"]), 2)
        # A rename of an existing object is not a creation: a later creation of its name keeps its check.
        renamed = required_checks(call([{"kind": "object_rename", "target": HANDLE, "parameters": {"name": "Pin"}},
                                        cube(name="Pin", location=[0, 0, 0])]))
        self.assertIn({"kind": "object_name", "target": "$box", "expected": "Pin"}, renamed)

    def test_set_transforms_state_their_channels_and_delta_states_nothing(self):
        checks = call([{"kind": "transform", "target": HANDLE, "parameters": {"mode": "SET", "scale": [2, 0.5, 1.5]}}])
        self.assertEqual(required_checks(checks), [{"kind": "transform", "target": HANDLE, "property": "scale",
                                                      "expected": [2.0, 0.5, 1.5]}])
        delta = call([{"kind": "transform", "target": HANDLE, "parameters": {"mode": "DELTA", "location": [1, 0, 0]}}])
        self.assertEqual(required_checks(delta), [])

    def test_each_number_compares_at_its_own_float32_precision(self):
        far = call([{"kind": "transform", "target": HANDLE, "parameters": {"location": [100000, 0, 0]}}])
        check = required_checks(far)[0]
        self.assertNotIn("tolerance", check, "Derived checks compare each number at its own precision")
        self.assertTrue(values_near([100000.0078, 0.0, 0.0], check["expected"], None), "One float32 ULP at 1e5")
        self.assertFalse(values_near([100000.0, 0.001, 0.0], check["expected"], None),
                         "The large number never loosens the small ones")

    def test_modifier_material_and_parent_settings(self):
        request = call([
            {"kind": "modifier_add", "target": HANDLE, "parameters": {"type": "BEVEL", "name": "Bevel",
                                                                     "settings": {"width": 0.125, "segments": 3}}},
            {"kind": "modifier_add", "target": HANDLE, "parameters": {"type": "MIRROR", "name": "Mirror",
                                                                     "settings": {"axes": [True, True, False]}}},
            {"kind": "material_principled", "target": HANDLE, "parameters": {
                "name": "Red", "inputs": {"Base Color": [1, 0, 0, 1], "Roughness": 0.3}}},
            {"kind": "parent_set", "target": HANDLE, "parameters": {"parent": OTHER}}],
            handles=(HANDLE, OTHER))
        checks = required_checks(request, material_facts=material_binding_facts(request))
        bevel, mirror, assignment, values, parent = checks
        # Only what each call states: the rest keeps Blender's defaults (action_schemas.SETTINGS_NOTE).
        self.assertEqual(bevel["values"], {"width": 0.125, "segments": 3})
        self.assertEqual(mirror["values"], {"use_axis": [True, True, False]})
        self.assertEqual({key: value for key, value in assignment.items() if key != "__host_material"},
                         {"kind": "material_assignment", "target": HANDLE, "material": "Red", "all_faces": False})
        # A new material on every face: its faces must render it (material_faces).
        self.assertEqual(assignment["__host_material"], {"resource": "created:2", "slot": 0, "rendered": True})
        self.assertEqual((values["base_color"], values["roughness"]), ([1.0, 0.0, 0.0, 1.0], 0.3))
        self.assertNotIn("metallic", values)
        self.assertEqual(parent, {"kind": "parent", "target": HANDLE, "expected": OTHER, "parent_type": "OBJECT"})
        boolean = call([{"kind": "modifier_add", "target": HANDLE, "parameters": {
            "type": "BOOLEAN", "settings": {"operand": OTHER}}}], handles=(HANDLE, OTHER))
        # The operand is a reference, not a setting; the closed settings are checked.
        self.assertEqual(len(required_checks(boolean)), 1)
        self.assertNotIn("operand", required_checks(boolean)[0]["values"])
        default_material = call([{"kind": "material_principled", "target": HANDLE, "parameters": {}}])
        self.assertEqual(required_checks(default_material, material_facts=material_binding_facts(default_material)), [],
                         "The default material name may collide")

    def test_later_operations_on_the_same_object_drop_what_they_may_change(self):
        checks = required_checks(call([cube(name="Box", size=2),
                       {"kind": "modifier_add", "target": "$box", "parameters": {"type": "SUBSURF", "name": "Smooth"}},
                       {"kind": "transform", "target": "$box", "parameters": {"scale": [2, 1, 1]}}],
                      handles=()))
        # The scale write drops the derived size; location and name survive it.
        self.assertEqual(kinds(checks), [("object_name", None), ("transform", "location"),
                                         ("modifier", "Smooth"), ("transform", "scale")])
        moved = required_checks(call([cube(name="Box", location=[1, 1, 1]),
                      {"kind": "transform", "target": "$box", "parameters": {"location": [5, 0, 0]}}],
                     handles=()))
        locations = [check["expected"] for check in moved if check.get("property") == "location"]
        self.assertEqual(locations, [[5.0, 0.0, 0.0]], "The final SET wins")
        self.assertIn(("dimensions", None), kinds(moved), "Moving never changes base-mesh dimensions")
        # A dimensions check reads evaluated bounds once the object has a modifier, so a modifier added after
        # the cube drops its derived size.
        arrayed = required_checks(call([cube(name="Box", size=2), {"kind": "modifier_add", "target": "$box", "parameters": {
            "type": "ARRAY", "settings": {"count": 5}}}], handles=()))
        self.assertNotIn(("dimensions", None), kinds(arrayed))
        shaded = required_checks(call([cube(name="Box", size=2), {"kind": "material_principled", "target": "$box",
                                                   "parameters": {"name": "Red"}}], handles=()))
        self.assertIn(("dimensions", None), kinds(shaded), "A material never changes dimensions")
        reconfigured = call([
            {"kind": "modifier_add", "target": HANDLE, "parameters": {"type": "BEVEL", "name": "Bevel"}},
            {"kind": "modifier_configure", "target": HANDLE, "parameters": {"type": "BEVEL", "modifier": "Bevel",
                                                                           "settings": {"width": 0.3}}}])
        self.assertEqual([(check["kind"], check["name"], check["values"]["width"]) for check in required_checks(reconfigured)],
                         [("modifier", "Bevel", 0.3)], "A typed configure writes every typed setting: its check replaces the add's")
        keyed = call([{"kind": "transform", "target": HANDLE, "parameters": {"location": [1, 2, 3]}},
                      {"kind": "keyframe_insert", "target": HANDLE, "parameters": {"property": "location",
                                                                                  "value": [0, 0, 0]}}])
        self.assertEqual(required_checks(keyed), [], "A keyframe writes the channel")

    def test_a_created_camera_and_light_state_their_place_settings_and_aim(self):
        """Regression: a model declared these itself, after a lookup round for the aim and settings check schemas."""
        camera = {"kind": "camera_create", "target": None, "parameters": {
            "name": "Deck Camera", "alias": "deck", "location": [-11, 1.4, 2.6], "look_at": [11, -.6, 1.6],
            "settings": {"lens": 24}}}
        checks = required_checks(call([camera], handles=()))
        self.assertEqual(checks, [
            {"kind": "object_name", "target": "$deck", "expected": "Deck Camera"},
            {"kind": "transform", "target": "$deck", "property": "location", "expected": [-11.0, 1.4, 2.6]},
            {"kind": "camera_settings", "target": "$deck", "values": {
                "lens": 24.0, "projection": "PERSP", "sensor_fit": "AUTO", "sensor_width": 36.0, "sensor_height": 24.0,
                "clip_start": 0.1, "clip_end": 1000.0}},
            {"kind": "aim", "target": "$deck", "point": [11.0, -0.6, 1.6], "tolerance": 0.0001}])
        sun = {"kind": "light_create", "target": None, "parameters": {
            "name": "Sun", "alias": "sun", "location": [8, -5, 1], "look_at": [0, 0, 0],
            "settings": {"light_type": "SUN", "energy": 7, "color": [1, .57, .28]}}}
        checks = required_checks(call([sun], handles=()))
        self.assertEqual(kinds(checks), [("object_name", None), ("transform", "location"), ("light_settings", None),
                                         ("aim", None)])
        self.assertEqual(checks[2]["values"]["energy"], 7.0)
        self.assertEqual(set(checks[2]["values"]), {"light_type", "energy", "color", "use_shadow", "angle"},
                         "Every setting of its type, as the approval shows them")

    def test_camera_and_light_checks_follow_what_the_operation_states(self):
        def created(kind, **parameters):
            return required_checks(call([{"kind": kind, "target": None, "parameters": dict({"alias": "it"}, **parameters)}],
                        handles=()))
        # A point light has no axis; an explicit rotation is checked as written; a default name is not.
        self.assertEqual(kinds(created("light_create", look_at=[0, 0, 0], location=[0, 0, 4])),
                         [("transform", "location"), ("light_settings", None)])
        self.assertEqual(kinds(created("camera_create", rotation=[1, 0, .5])),
                         [("transform", "location"), ("transform", "rotation_euler"), ("camera_settings", None)])
        self.assertEqual(kinds(created("light_create", settings={"light_type": "SPOT"}, look_at=[0, 0, 0],
                                       location=[0, 0, 4], name="Key")),
                         [("object_name", None), ("transform", "location"), ("light_settings", None), ("aim", None)])
        unnamed = call([{"kind": "camera_create", "target": None, "parameters": {"location": [0, 0, 1]}}], handles=())
        self.assertEqual(required_checks(unnamed), [], "Without an alias nothing can be checked")

    def test_later_operations_drop_the_camera_checks_they_may_falsify(self):
        camera = {"kind": "camera_create", "target": None, "parameters": {
            "name": "Shot", "alias": "shot", "location": [0, -6, 2], "look_at": [0, 0, 1]}}

        def after(kind, **parameters):
            return kinds(required_checks(call([camera, {"kind": kind, "target": "$shot", "parameters": parameters}],
                              handles=(HANDLE,))))
        moved = after("transform", location=[1, -6, 2])
        self.assertNotIn(("aim", None), moved, "Moving it changes what it looks at")
        self.assertIn(("camera_settings", None), moved)
        reconfigured = after("camera_configure", settings={"lens": 85})
        self.assertNotIn(("camera_settings", None), reconfigured)
        self.assertNotIn(("aim", None), reconfigured)
        parented = after("parent_set", parent=HANDLE)
        self.assertNotIn(("aim", None), parented)
        self.assertIn(("camera_settings", None), parented)
        renamed = after("object_rename", name="Close-up")
        self.assertIn(("aim", None), renamed)
        self.assertIn(("camera_settings", None), renamed)
        # A scene setting naming the camera is another object's operation: nothing of the camera is dropped.
        scene = [camera, {"kind": "property_set", "target": HANDLE, "parameters": {"values": {"camera": "$shot"}}}]
        self.assertIn(("aim", None), kinds(required_checks(call(scene, handles=(HANDLE,)))))

    def test_every_keep_table_names_only_real_action_kinds(self):
        from angelo_agent import derived_camera_light
        for table in (derived_camera_light.SETTINGS_KEEPS, derived_camera_light.AIM_KEEPS):
            self.assertLessEqual(table, frozenset(OPERATION_SCHEMAS))
        tables = [derived_checks.NAME_KEEPS, derived_checks.TRANSFORM_KEEPS, derived_checks.DIMENSIONS_KEEPS,
                  derived_checks.MODIFIER_KEEPS, derived_checks.MATERIAL_KEEPS, derived_checks.PARENT_KEEPS]
        for table in tables:
            self.assertLessEqual(table, frozenset(OPERATION_SCHEMAS))
        self.assertEqual(set(derived_checks.MODIFIER_FIELDS), {"BEVEL", "SUBSURF", "SOLIDIFY", "MIRROR", "ARRAY", "DECIMATE",
                                                              "WEIGHTED_NORMAL", "TRIANGULATE", "SHRINKWRAP", "DISPLACE",
                                                              "SCREW", "CURVE", "BOOLEAN", "REMESH"})

    def test_declared_checks_coexist_and_normalization_is_idempotent(self):
        declared = {"kind": "transform", "target": HANDLE, "property": "scale", "expected": [2, 2, 2],
                    "tolerance": 0.01}
        checks = call([{"kind": "transform", "target": HANDLE, "parameters": {"scale": [2, 2, 2]}}], [declared])
        self.assertEqual(required_checks(checks)[0], declared)
        self.assertEqual(len(required_checks(checks)), 2, "The mandatory stricter tolerance remains")
        again = validate_call("angelo_scene_program", checks)
        self.assertEqual(again, checks)
        self.assertEqual(validate_program("angelo_scene_program", checks), checks,
                         "Internal re-validation adds nothing")

    def test_restating_public_checks_never_suppresses_private_identity_obligations(self):
        """Public equality dedupes; strict authored names and private identity guards coexist."""
        operations = [
            cube(name="Railing Posts South", alias="posts", location=[-12, -2.4, 1.5], scale=[.05, .05, .5]),
            {"kind": "modifier_add", "target": "$posts", "parameters": {
                "name": "Railing 17 Posts", "type": "ARRAY", "settings": {"count": 17, "relative_offset_displace": [15, 0, 0]}}},
            {"kind": "material_principled", "target": "$posts", "parameters": {
                "name": "Railing Dark Iron", "inputs": {"Base Color": [.018, .021, .024, 1], "Metallic": .8, "Roughness": .36}}},
            {"kind": "transform", "target": HANDLE, "parameters": {"location": [0, 0, 3]}},
            {"kind": "parent_set", "target": "$posts", "parameters": {"parent": HANDLE}},
            {"kind": "camera_create", "target": None, "parameters": {"name": "Deck Camera", "alias": "deck",
                                                                     "location": [-11, 1.4, 2.6], "look_at": [11, -.6, 1.6]}},
            {"kind": "property_set", "target": OTHER, "parameters": {"values": {"frame_end": 120}}}]
        omitted = required_checks(call(operations, handles=(HANDLE, OTHER)))
        self.assertEqual(kinds(omitted), [
            ("object_name", None), ("modifier", "Railing 17 Posts"), ("material_assignment", "Railing Dark Iron"),
            ("material_values", "Railing Dark Iron"), ("transform", "location"), ("parent", None), ("object_name", None),
            ("transform", "location"), ("camera_settings", None), ("aim", None), ("property", None)])
        self.assertTrue(all(resolve_obligations(call(operations, handles=(HANDLE, OTHER))).derived_flags))
        # A model that restates every one of them, in its own order and with a looser default tolerance.
        public = [{key: value for key, value in check.items() if key != "__host_material"} for check in omitted]
        restated = required_checks(call(operations, list(reversed(copy.deepcopy(public))), handles=(HANDLE, OTHER)))
        key = lambda check: json.dumps(check, sort_keys=True)
        guarded = [check for check in omitted if "__host_material" in check]
        self.assertEqual(sorted(restated, key=key), sorted(public + guarded, key=key))
        self.assertTrue(all(check in restated for check in guarded), "Private name projection cannot weaken authored names")
        # What only the model can know (the array's overall size) is added to the same derived set, never instead.
        extent = {"kind": "dimensions", "target": "$posts", "expected": [24.1, .1, 1], "tolerance": .0001, "space": "WORLD"}
        declared = required_checks(call(operations, [extent], handles=(HANDLE, OTHER)))
        self.assertEqual(declared[0], extent)
        self.assertEqual(sorted(declared[1:], key=key), sorted(omitted, key=key))

    def test_derived_checks_have_a_separate_allowance_and_strict_schema(self):
        filler = [{"kind": "object_count", "minimum": 0, "maximum": index + 1} for index in range(MAX_CHECKS - 1)]
        checks = required_checks(call([cube(name="Box", size=2)], filler, handles=()))
        self.assertEqual(len(checks), MAX_CHECKS + 2)
        self.assertEqual(checks[-1]["kind"], "dimensions")
        self.assertEqual([validate_check(check) for check in checks], checks)
        for bad in ([{"kind": "transform", "target": HANDLE, "property": "location", "expected": [0, 0, 0],
                      "tolerance": 0.1, "derived": True}],
                    [{"kind": "python", "code": "pass"}],
                    [{"kind": "object_name", "target": "$missing", "expected": "x"}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                call([{"kind": "transform", "target": HANDLE, "parameters": {"location": [1, 0, 0]}}], bad)
        with self.assertRaises(ValueError):
            call([{"kind": "transform", "target": HANDLE, "parameters": {"location": [1, 0, 0]}}],
                 [{"kind": "object_count", "minimum": 0, "maximum": 1}] * (MAX_CHECKS + 1))

    def test_an_unschematic_derived_check_refuses_before_staging(self):
        from unittest.mock import patch
        invalid = {"kind": "object_name", "target": "$box", "expected": "x" * 65}
        with patch("angelo_agent.derived_checks.derive_checks", return_value=[invalid]):
            with self.assertRaisesRegex(ValueError, "mandatory check.*split"):
                call([cube(name="Box")], handles=())

    def test_recipe_expansions_are_left_exactly_as_expanded(self):
        from angelo_agent.action_recipes import expand_recipe
        expansion = expand_recipe("material-setup", HANDLE, {"name": "QA", "base_color": [1, 0, 0, 1],
                                                             "roughness": 0.3, "metallic": 0.4, "tolerance": 0.0001})
        program = {"mode": "APPLY", "read_set": [{"handle": HANDLE, "revision": REVISION}],
                   "write_set": [{"handle": HANDLE, "revision": REVISION}], "operations": expansion["operations"],
                   "expected_checks": expansion["expected_checks"]}
        self.assertEqual(validate_program("angelo_scene_program", program)["expected_checks"],
                         expansion["expected_checks"])

    def test_guidance_and_schema_tell_the_model_what_it_may_omit(self):
        from angelo_agent.providers import CLAUDE_PLAN_INSTRUCTIONS, INSTRUCTIONS, codex_instructions
        # Facts-only text: the host's check of stated values and what expected_checks adds, without
        # "never restate one".
        contract = ("The host itself checks every value an operation states (names, transforms, modifier, material, "
                    "camera, light and scene settings, parents). expected_checks adds checks of results no operation "
                    "states")
        # Every Codex runtime reads the same sentence: a Codex plan thread, voice and external threads.
        for text in (INSTRUCTIONS, codex_instructions(short_identifiers=True, deferred=True, plan=True)):
            self.assertIn(contract, " ".join(text.split()))
        # A Claude plan reads the Claude per-model text (claude_text).
        self.assertIn("declare only the expected_checks its operations do not imply", CLAUDE_PLAN_INSTRUCTIONS)
        schema = TOOL_MAP["angelo_scene_program"]["inputSchema"]["properties"]["expected_checks"]
        self.assertIn("The host checks what the operations imply itself: never restate it.", schema["description"])


class DerivedCheckExecutionTests(unittest.TestCase):
    """Derived checks run exactly where declared ones do: staging, commit and Accept."""

    def setUp(self):
        self.backend, self.writer, self.previews = Backend(), WriterLease(), PreviewStore()
        self.checked = []
        validate = self.backend.validate

        def recording(stage, check):
            self.checked.append(copy.deepcopy(check))
            return validate(stage, check)
        self.backend.validate = recording

    def program(self, mode="APPLY"):
        return call([{"kind": "transform", "target": HANDLE, "parameters": {"location": [2, 3, 4]}}], mode=mode)

    def test_a_derived_check_is_verified_on_the_stage_and_again_before_commit(self):
        arguments = self.program()
        self.assertEqual(len(required_checks(arguments)), 1)
        job = ProgramJob(self.backend, arguments, owner="chat", writer=self.writer, previews=self.previews)
        self.assertTrue(finish(job)["ready_to_commit"])
        result = job.commit()
        self.assertEqual(result["status"], "committed")
        self.assertEqual([check["expected"] for check in self.checked], [[2.0, 3.0, 4.0]] * 2)

    def test_a_failing_derived_check_blocks_the_commit(self):
        apply = self.backend.apply

        def wrong(stage, operation):
            apply(stage, operation)
            stage["copies"][HANDLE]["location"] = [0, 0, 0]  # The operation did not land as stated.
            return {"changed": True}
        self.backend.apply = wrong
        job = ProgramJob(self.backend, self.program(), owner="chat", writer=self.writer, previews=self.previews)
        result = finish(job)
        self.assertFalse(result["success"])
        self.assertIn("Expected check failed", result["error"])
        self.assertEqual(self.backend.commits, 0)

    def test_accept_revalidates_derived_checks(self):
        job = ProgramJob(self.backend, self.program("PREVIEW"), owner="chat", writer=self.writer, previews=self.previews)
        preview = finish(job)
        accepted = ProgramJob.accept(preview["preview_id"], "chat", previews=self.previews, writer=self.writer)
        finish(accepted)
        self.assertEqual(accepted.commit()["status"], "committed")
        self.assertGreaterEqual(len(self.checked), 2)


def outcome(kind, observed):
    row = {"index": 0, "kind": kind, "target": "$box", "status": "pass", "passed": True, "observed": observed}
    return {"success": True, "passed": True, "status": "pass", "checks": [row], "kind": kind, "message": kind}


class DerivedCheckReceiptTests(unittest.TestCase):
    def operation(self, checks=()):
        return {"tool": "angelo_scene_program", "arguments": {
            "mode": "APPLY", "read_set": [], "write_set": [],
            "operations": [cube(name="Eval Cube", size=2)], "expected_checks": list(checks)}}

    def result(self, outcomes):
        return {"success": True, "final": True, "status": "committed", "scene_changed": True, "undoable": True,
                "changes": {"added": [{"name": "Eval Cube", "type": "MESH"}], "modified": [], "removed": []},
                "live_revisions": {"status": "COMMITTED", "objects": [
                    {"handle": HANDLE, "name": "Eval Cube", "type": "MESH", "role": "CREATED", "alias": "$box",
                     "object_revision": REVISION}]},
                "checks": outcomes}

    def test_a_plan_without_declared_checks_is_certified_by_its_derived_ones(self):
        outcomes = [outcome("object_name", {"name": "Eval Cube"}),
                    outcome("transform", {"location": [0, 0, 0], "scope": "local_RNA"}),
                    outcome("dimensions", {"dimensions": [2, 2, 2], "space": "WORLD", "scope": "BASE_MESH"})]
        text = certified_completion([self.operation()], [self.result(outcomes)],
                                    read_only=lambda name: TOOL_MAP[name]["annotations"]["readOnlyHint"])
        self.assertIn("Host checks passed on the live result (3 of 3; 3 implied by the operations):", text)
        self.assertIn("- dimensions on 'Eval Cube': dimensions [2, 2, 2]; space 'WORLD'", text)

    def test_a_missing_derived_outcome_is_never_certified(self):
        outcomes = [outcome("object_name", {"name": "Eval Cube"})]
        self.assertIsNone(certified_completion([self.operation()], [self.result(outcomes)],
                                               read_only=lambda name: False))


if __name__ == "__main__":
    unittest.main()
