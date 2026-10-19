# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete host obligations stay separate from the bounded public request.

Pure host tests cover legal typed programs, nested conjunctions and admission
failures. They do not claim Blender RNA or live commit coverage.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_checks  # noqa: E402
from angelo_agent.approvals import approval_details, review_arguments  # noqa: E402
from angelo_agent.program_context import ProgramBinding, precondition_change  # noqa: E402
from angelo_agent.program_jobs import PreviewStore, ProgramError, ProgramJob, WriterLease  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, validate_call  # noqa: E402
from angelo_agent.validation_spec import CHECKS_SCHEMA, validate_check, validate_checks  # noqa: E402

TARGET = "scn:" + "a" * 32 + ":7"
REVISION = "b" * 64


def cube(index):
    return {"kind": "create_primitive", "target": None,
            "parameters": {"primitive": "CUBE", "name": "Part %02d" % index, "alias": "b%d" % index}}


def program(operations, checks=(), *, bound=False):
    return {"mode": "APPLY", "operations": operations, "expected_checks": list(checks),
            "write_set": [{"handle": TARGET, "revision": REVISION}] if bound else []}


def canonical(operations, checks=(), *, bound=False):
    return validate_call("angelo_scene_program", program(operations, checks, bound=bound))


def node_edit(edits):
    return {"kind": "node_edit", "target": TARGET, "parameters": {"material": "Wood", "edits": edits}}


def property_set(values):
    return {"kind": "property_set", "target": TARGET, "parameters": {"values": values}}


def maximum_material_program():
    """32 distinct primary materials plus 31 shared recipients: 1,056 obligations."""
    handles = ["scn:" + "a" * 32 + ":%d" % index for index in range(1, 64)]
    operations = [{"kind": "material_principled", "target": target,
                   "parameters": {"name": "Material %02d" % index, "inputs": {"Roughness": .25}, "also": handles[32:]}}
                  for index, target in enumerate(handles[:32])]
    authored = [{"kind": "object_count", "minimum": 63, "maximum": 63, "types": []} for _ in range(32)]
    request = program(operations, authored)
    request["write_set"] = [{"handle": handle, "revision": REVISION} for handle in handles]
    return validate_call("angelo_scene_program", request)


def material_binding_facts(request, *, material=None):
    """Private stand-in facts, passed to the host resolver rather than public args."""
    owners = [{"target": entry["handle"], "slot_count": int(material is not None),
               "slots": ([{"slot": 0, "resource": "live:7", "name": material, "link": "OBJECT"}]
                         if material is not None else [])} for entry in request["write_set"]]
    resources = {"live:7": {"uid": 7, "name": material, "principled": None, "groups": {}, "sharing": {}}} if material else {}
    return {"owners": owners, "resources": resources}


def bound_checks(request, *, material=None):
    return program_checks.required_checks(request, material_facts=material_binding_facts(request, material=material))


class PublicBoundaryTests(unittest.TestCase):
    def test_public_program_and_standalone_verification_keep_32_check_limit(self):
        self.assertEqual(TOOL_MAP["angelo_scene_program"]["inputSchema"]["properties"]["expected_checks"]["maxItems"], 32)
        self.assertEqual(CHECKS_SCHEMA["maxItems"], 32)
        implied = program_checks.required_checks(canonical([cube(index) for index in range(11)]))
        self.assertEqual(len(implied), 33)
        arbitrary = [{"kind": "object_count", "minimum": 0, "maximum": 100}] * 33
        for supplied in (implied, arbitrary):
            for derive in (False, True):
                with self.subTest(implied=supplied is implied, derive=derive), self.assertRaisesRegex(ValueError, "32"):
                    validate_program(program([cube(index) for index in range(11)], supplied), derive_checks=derive)
            with self.assertRaises(ValueError):
                validate_checks(supplied)

    def test_32_cubes_keep_all_96_checks_including_last_object(self):
        request = canonical([cube(index) for index in range(32)])
        checks = program_checks.required_checks(request)
        self.assertEqual(request["expected_checks"], [])
        self.assertEqual(len(checks), 96)
        self.assertEqual({check["target"] for check in checks}, {"$b%d" % index for index in range(32)})
        self.assertEqual({check["kind"] for check in checks if check["target"] == "$b31"},
                         {"object_name", "transform", "dimensions"})

    def test_31_cubes_and_material_also_keep_all_125_checks(self):
        operations = [cube(index) for index in range(31)] + [{
            "kind": "material_principled", "target": "$b0", "parameters": {
                "name": "Copper", "also": ["$b%d" % index for index in range(1, 31)],
                "inputs": {"Base Color": [.8, .3, .1, 1], "Metallic": .9, "Roughness": .3}}}]
        checks = program_checks.required_checks(canonical(operations))
        self.assertEqual(len(checks), 125)
        assigned = {check["target"] for check in checks if check["kind"] == "material_assignment"}
        self.assertEqual(assigned, {"$b%d" % index for index in range(31)})
        self.assertTrue(any(check["target"] == "$b30" and check["kind"] == "material_assignment" for check in checks))

    def test_32_authored_checks_do_not_displace_any_mandatory_check(self):
        authored = [{"kind": "object_count", "minimum": 0, "maximum": index + 32, "types": []} for index in range(32)]
        request = canonical([cube(index) for index in range(32)], authored)
        checks = program_checks.required_checks(request)
        self.assertEqual(request["expected_checks"], authored)
        self.assertEqual(checks[:32], authored)
        self.assertEqual(len(checks), 128)
        self.assertTrue(any(check.get("target") == "$b31" for check in checks))

    def test_32_distinct_material_primaries_reach_the_true_1088_check_limit(self):
        request = maximum_material_program()
        checks = bound_checks(request)
        self.assertEqual(len(request["write_set"]), 63)
        self.assertEqual(len(request["operations"]), 32)
        self.assertEqual(len(request["expected_checks"]), 32)
        self.assertEqual(len(checks), 1088)
        self.assertEqual(sum(check["kind"] == "material_assignment" for check in checks), 1024)
        self.assertEqual(sum(check["kind"] == "material_values" for check in checks), 32)
        self.assertEqual(checks[:32], request["expected_checks"])
        self.assertEqual(validate_call("angelo_scene_program", request), request)

    def test_weak_and_conflicting_authored_checks_cannot_replace_required_values(self):
        operation = {"kind": "transform", "target": TARGET, "parameters": {"location": [2, 3, 4]}}
        for expected in ([2, 3, 4], [9, 9, 9]):
            authored = {"kind": "transform", "target": TARGET, "property": "location",
                        "expected": expected, "tolerance": .1}
            checks = program_checks.required_checks(canonical([operation], [authored], bound=True))
            with self.subTest(expected=expected):
                self.assertEqual(len(checks), 2)
                self.assertEqual(checks[0], authored)
                self.assertEqual(checks[1]["expected"], [2, 3, 4])
                self.assertNotIn("tolerance", checks[1], "Each number at its own float32 precision, never .1")

    def test_partial_authored_map_cannot_suppress_other_written_fields(self):
        authored = {"kind": "property", "target": TARGET, "values": {"hide_render": False}}
        checks = program_checks.required_checks(canonical(
            [property_set({"hide_render": False, "show_name": True})], [authored], bound=True))
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[1]["values"], {"hide_render": False, "show_name": True})

    def test_exact_numeric_restatement_deduplicates_but_booleans_do_not(self):
        authored = {"kind": "transform", "target": TARGET, "property": "location",
                    "expected": [2, 3, 4], "tolerance": .00001}
        transform = {"kind": "transform", "target": TARGET, "parameters": {"location": [2., 3., 4.]}}
        self.assertEqual(program_checks.required_checks(canonical([transform], [authored], bound=True)), [authored])
        # Generic RNA settings retain their JSON type before live RNA validation.
        authored = {"kind": "property", "target": TARGET, "values": {"setting_value": 1}}
        checks = program_checks.required_checks(canonical([property_set({"setting_value": True})], [authored], bound=True))
        self.assertEqual(len(checks), 2)
        self.assertIs(type(checks[0]["values"]["setting_value"]), int)
        self.assertIs(type(checks[1]["values"]["setting_value"]), bool)

    def test_normalization_check_derivation_and_digest_are_stable_without_mutation(self):
        request = canonical([cube(index) for index in range(32)])
        before = json.dumps(request, sort_keys=True, separators=(",", ":"))
        checks = program_checks.required_checks(request)
        digest = program_checks.checks_digest(checks)
        self.assertEqual(validate_call("angelo_scene_program", request), request)
        self.assertEqual(validate_program(request, derive_checks=True), request)
        self.assertEqual(program_checks.required_checks(request), checks)
        self.assertEqual(program_checks.checks_digest(program_checks.required_checks(request)), digest)
        self.assertEqual(json.dumps(request, sort_keys=True, separators=(",", ":")), before)
        checks[-1]["expected"][0] += 1
        self.assertNotEqual(program_checks.checks_digest(checks), digest)
        self.assertEqual(program_checks.checks_digest(program_checks.required_checks(request)), digest)


class NestedObligationTests(unittest.TestCase):
    def assert_strict(self, checks):
        for check in checks:
            public = {key: value for key, value in check.items() if key != "__host_material"}
            self.assertEqual(validate_check(public), public)
            if "__host_material" in check:
                with self.assertRaises(ValueError):
                    validate_check(check)

    def test_two_parent_steps_keep_all_64_nodes(self):
        names = ["Node %02d" % index for index in range(64)]
        edits = [{"op": "parent", "nodes": names[start:start + 32], "frame": "Frame"} for start in (0, 32)]
        checks = bound_checks(canonical([node_edit(edits)], bound=True), material="Wood")
        self.assertEqual(len(checks), 2)
        nodes = [node for check in checks for node in check["nodes"]]
        self.assertEqual({node["name"] for node in nodes}, set(names))
        self.assertTrue(all(node["parent"] == "Frame" for node in nodes))
        self.assert_strict(checks)

    def test_largest_schema_parent_expansion_is_packed_without_losing_a_node(self):
        # This proves typed-check packing, not admission of a 1024-node live
        # tree: the native graph reader has its own smaller scene bound.
        names = ["Node %04d" % index for index in range(1024)]
        edits = [{"op": "parent", "nodes": names[start:start + 32], "frame": "Frame"}
                 for start in range(0, 1024, 32)]
        checks = bound_checks(canonical([node_edit(edits)], bound=True), material="Wood")
        self.assertEqual(len(checks), 32)
        self.assertEqual([node["name"] for check in checks for node in check["nodes"]], names)
        self.assert_strict(checks)

    def test_repeated_set_steps_keep_all_64_input_values_on_one_node(self):
        values = {"Input %02d" % index: index for index in range(64)}
        items = list(values.items())
        edits = [{"op": "set", "node": "Group", "inputs": dict(items[start:start + 32])} for start in (0, 32)]
        checks = bound_checks(canonical([node_edit(edits)], bound=True), material="Wood")
        self.assertEqual(len(checks), 1)
        nodes = [node for check in checks for node in check["nodes"]]
        self.assertEqual({key: value for node in nodes for key, value in node["inputs"].items()}, values)
        self.assertEqual({node["name"] for node in nodes}, {"Group"})
        self.assert_strict(checks)

    def test_merged_scene_settings_keep_every_actual_path(self):
        values = {"render.resolution_x": 1920, "render.resolution_y": 1080, "render.resolution_percentage": 100,
                  "render.pixel_aspect_x": 1., "render.pixel_aspect_y": 1., "render.film_transparent": True,
                  "render.fps": 24, "render.fps_base": 1., "render.use_motion_blur": False,
                  "render.use_simplify": False, "render.simplify_subdivision": 2, "render.use_border": False,
                  "render.use_crop_to_border": False, "render.use_persistent_data": False,
                  "render.use_compositing": True, "render.use_sequencer": True, "render.use_freestyle": False,
                  "render.use_high_quality_normals": True, "render.use_simplify_normals": False,
                  "render.simplify_subdivision_render": 2, "render.simplify_child_particles": 1.,
                  "render.motion_blur_shutter": .5, "render.motion_blur_position": "CENTER",
                  "render.dither_intensity": 1., "frame_start": 1, "frame_end": 120, "frame_step": 1,
                  "frame_preview_start": 1, "frame_preview_end": 120, "use_preview_range": False,
                  "use_gravity": True, "audio_volume": 1., "view_settings.exposure": 0., "view_settings.gamma": 1.}
        items = list(values.items())
        operations = [property_set(dict(items[start:start + 32])) for start in range(0, len(items), 32)]
        checks = program_checks.required_checks(canonical(operations, bound=True))
        self.assertEqual(len(checks), 2)
        self.assertEqual({key: value for check in checks for key, value in check["values"].items()}, values)
        self.assert_strict(checks)

    def test_merged_bone_and_particle_maps_keep_all_schema_admitted_fields(self):
        # These generic paths are admitted without consulting Blender RNA. The
        # live kernel separately checks whether the installed RNA owns them.
        values = {"setting_%03d" % index: index for index in range(65)}
        items = list(values.items())
        for kind, field, identifier in (("bone_set", "settings", {"bone": "Spine"}),
                                        ("bone_set", "pose_settings", {"bone": "Spine"}),
                                        ("particle_set", "settings", {"modifier": "Grass"})):
            operations = [{"kind": kind, "target": TARGET, "parameters": dict(identifier, **{field: dict(items[start:start + 64])})}
                          for start in (0, 64)]
            checks = program_checks.required_checks(canonical(operations, bound=True))
            output_field = "values" if kind == "particle_set" else field
            with self.subTest(kind=kind, field=field):
                self.assertEqual(len(checks), 2)
                self.assertEqual({key: value for check in checks for key, value in check[output_field].items()}, values)
                self.assert_strict(checks)

    def test_32_collection_links_and_unlinks_preserve_every_membership(self):
        names = ["Collection %02d" % index for index in range(32)]
        for mode, field, other in (("LINK", "collections", "absent"), ("UNLINK", "absent", "collections")):
            operations = [{"kind": "collection_link", "target": TARGET,
                           "parameters": {"collection": name, "mode": mode}} for name in names]
            checks = program_checks.required_checks(canonical(operations, bound=True))
            with self.subTest(mode=mode):
                self.assertEqual(len(checks), 2)
                self.assertEqual([name for check in checks for name in check[field]], names)
                self.assertTrue(all(not check[other] for check in checks))
                self.assert_strict(checks)

    def test_32_modifier_removals_preserve_every_absent_name(self):
        names = ["Modifier %02d" % index for index in range(32)]
        operations = [{"kind": "modifier_remove", "target": TARGET, "parameters": {"modifier": name}}
                      for name in names]
        checks = program_checks.required_checks(canonical(operations, bound=True))
        self.assertEqual(len(checks), 2)
        self.assertEqual([name for check in checks for name in check["absent"]], names)
        self.assert_strict(checks)

    def test_repeated_batches_on_same_existing_source_keep_each_copy_group(self):
        batch = {"kind": "batch", "target": None, "parameters": {
            "sources": [TARGET], "array": [{"step": [1, 0, 0], "count": 3}]}}
        checks = program_checks.required_checks(canonical([batch, copy.deepcopy(batch)], bound=True))
        self.assertEqual([check["alias"] for check in checks], ["$copy1_0", "$copy2_0"])
        self.assertEqual([check["count"] for check in checks], [2, 2])
        self.assertEqual({check["target"] for check in checks}, {TARGET})
        self.assert_strict(checks)

    def test_later_property_alias_material_and_node_writes_define_final_obligations(self):
        properties = program_checks.required_checks(canonical([
            property_set({"hide_render": False, "show_name": True}), property_set({"hide_render": True})], bound=True))
        self.assertEqual([check["values"] for check in properties], [{"hide_render": True, "show_name": True}])
        alias = program_checks.required_checks(canonical([
            cube(0), {"kind": "transform", "target": "$b0", "parameters": {"scale": [2, 3, 4]}}]))
        self.assertNotIn("dimensions", [check["kind"] for check in alias])
        self.assertEqual([check["expected"] for check in alias if check.get("property") == "scale"], [[2, 3, 4]])
        materials = bound_checks(canonical([
            {"kind": "material_principled", "target": TARGET, "parameters": {"name": name}}
            for name in ("Old", "Final")], bound=True))
        self.assertEqual({check["material"] for check in materials}, {"Old", "Final"},
                         "A new append slot preserves the earlier material and its stated values")
        nodes = bound_checks(canonical([node_edit([
            {"op": "set", "node": "Noise", "inputs": {"Scale": 1, "Detail": 4}},
            {"op": "set", "node": "Noise", "inputs": {"Scale": 2}}])], bound=True), material="Wood")
        self.assertEqual(nodes[0]["nodes"], [{"name": "Noise", "inputs": {"Scale": 2, "Detail": 4}}])


class RecordingBackend:
    """Exercise host scheduling and refusal without pretending to evaluate RNA."""

    def __init__(self):
        self.checked = []
        self.fail_target = None
        self.commits = 0
        self.discards = 0

    def ensure_main_thread(self):
        pass

    def bind(self, program):
        return {"handles": []}

    def create_stage(self, binding):
        return {}

    def finish_stage(self, stage):
        pass

    def apply(self, stage, operation):
        return {}

    def validate(self, stage, check):
        self.checked.append(copy.deepcopy(check))
        passed = check.get("target") != self.fail_target
        row = {"kind": check["kind"], "target": check.get("target"), "passed": passed,
               "status": "pass" if passed else "fail"}
        return dict(row, checks=[row])

    def validate_stage(self, stage):
        pass

    def delta(self, stage):
        return {"summary": "Host scheduling fixture"}

    def verify(self, binding):
        pass

    def check_context(self, binding):
        pass

    def commit(self, stage):
        self.commits += 1
        return {}

    def discard(self, stage):
        self.discards += 1


def settle(job):
    for _ in range(32 + program_checks.MAX_PROGRAM_CHECKS + 10):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Host fixture did not settle within its bounded work")


class RuntimeBoundaryTests(unittest.TestCase):
    def make_job(self, mode="APPLY"):
        backend, previews, writer = RecordingBackend(), PreviewStore(), WriterLease()
        request = canonical([cube(index) for index in range(32)])
        request["mode"] = mode
        job = ProgramJob(backend, request, owner="test", previews=previews, writer=writer)
        return job, backend, previews, writer

    def test_last_object_failure_stops_staged_validation(self):
        job, backend, _, writer = self.make_job()
        backend.fail_target = "$b31"
        result = settle(job)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(backend.checked[-1]["target"], "$b31")
        self.assertGreater(len(backend.checked), 32)
        self.assertEqual(backend.commits, 0)
        self.assertEqual(backend.discards, 1)
        self.assertIsNone(writer.owner)

    def test_last_object_failure_is_rechecked_immediately_before_commit(self):
        job, backend, _, writer = self.make_job()
        self.assertTrue(settle(job)["ready_to_commit"])
        self.assertEqual(len(backend.checked), 96)
        backend.checked.clear()
        backend.fail_target = "$b31"
        result = job.commit()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(backend.checked[-1]["target"], "$b31")
        self.assertGreater(len(backend.checked), 32)
        self.assertEqual(backend.commits, 0)
        self.assertIsNone(writer.owner)

    def test_preview_accept_rechecks_the_last_object(self):
        job, backend, previews, writer = self.make_job("PREVIEW")
        result = settle(job)
        self.assertEqual(result["status"], "preview")
        record = previews.get(result["preview_id"], "test")
        self.assertEqual(record["checks"], job.checks)
        self.assertEqual(record["checks_digest"], program_checks.checks_digest(job.checks))
        backend.checked.clear()
        backend.fail_target = "$b31"
        accept = ProgramJob.accept(result["preview_id"], "test", previews=previews, writer=writer)
        failed = settle(accept)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(backend.checked[-1]["target"], "$b31")
        self.assertGreater(len(backend.checked), 32)
        self.assertEqual(backend.commits, 0)
        self.assertNotIn(result["preview_id"], previews.records)
        self.assertIsNone(writer.owner)

    def test_mutating_the_frozen_check_list_cannot_remove_precommit_obligations(self):
        job, backend, _, writer = self.make_job()
        self.assertTrue(settle(job)["ready_to_commit"])
        job.checks.pop()
        result = job.commit()
        self.assertEqual(result["status"], "failed")
        self.assertIn("verification", result["error"])
        self.assertEqual(backend.commits, 0)
        self.assertIsNone(writer.owner)

    def test_preview_with_changed_required_checks_cannot_acquire_writer_or_commit(self):
        job, backend, previews, writer = self.make_job("PREVIEW")
        identity = settle(job)["preview_id"]
        record = previews.get(identity, "test")
        record["checks"].pop()
        with self.assertRaisesRegex(ProgramError, "verification"):
            ProgramJob.accept(identity, "test", previews=previews, writer=writer)
        self.assertIsNone(writer.owner)
        self.assertEqual(backend.commits, 0)
        if identity in previews.records:
            previews.discard(identity, "test")
        self.assertEqual(backend.discards, 1)

    def test_native_preview_precondition_discards_changed_obligations(self):
        from angelo_agent import native_programs
        job, backend, previews, writer = self.make_job("PREVIEW")
        identity = settle(job)["preview_id"]
        previews.get(identity, "test")["checks"].pop()
        with patch.object(native_programs, "PREVIEWS", previews), \
                patch.object(native_programs, "_owner", return_value="test"), \
                patch.object(native_programs, "BlenderProgramBackend", return_value=backend), \
                patch.object(native_programs, "require_cleanup_complete"):
            with self.assertRaisesRegex(ProgramError, "verification"):
                native_programs.precondition(None, "angelo_accept_preview", {"preview_id": identity})
        self.assertNotIn(identity, previews.records)
        self.assertEqual(backend.discards, 1)
        self.assertEqual(backend.commits, 0)
        self.assertIsNone(writer.owner)

    def test_preview_obligations_are_checked_before_stage_ownership_transfer(self):
        backend, previews = RecordingBackend(), PreviewStore()
        request = canonical([cube(0)])
        checks = program_checks.required_checks(request)[:-1]
        with patch.object(backend, "retain_stage", create=True) as retain:
            with self.assertRaisesRegex(ProgramError, "verification"):
                previews.retain(backend, {}, {}, request, {}, "test", checks)
        retain.assert_not_called()
        self.assertEqual(backend.discards, 0, "A rejected transfer leaves the stage with its caller")
        self.assertEqual(previews.records, {})

    def test_changed_verification_digest_is_never_frame_rebindable(self):
        before = ProgramBinding(1, 2, (1, 0.), 3, (), None, "a" * 64)
        for after in (before._replace(checks_digest="b" * 64),
                      before._replace(checks_digest="b" * 64, frame=(2, 0.))):
            change = precondition_change(before, after)
            self.assertEqual(change["kind"], "verification")
            self.assertFalse(change["rebindable"])

    def test_approval_details_show_all_checks_without_mutating_submitted_arguments(self):
        request = canonical([cube(index) for index in range(32)])
        before = copy.deepcopy(request)
        for name in ("angelo_scene_program", "angelo_apply_recipe"):
            arguments = request if name == "angelo_scene_program" else {"recipe": "material-setup", "parameters": {}}
            call = SimpleNamespace(name=name, arguments=arguments,
                                   program_arguments=request if name == "angelo_apply_recipe" else None)
            reviewed = review_arguments(call)
            shown = reviewed if name == "angelo_scene_program" else reviewed["program"]
            self.assertEqual(len(shown["expected_checks"]), 96)
            detail = approval_details(name, reviewed)
            self.assertIn("96", detail)
            self.assertIn("$b31", detail)
            self.assertEqual(request, before)
            shown["expected_checks"].pop()
            self.assertEqual(request, before)


class AdmissionBudgetTests(unittest.TestCase):
    def test_budget_contract_is_explicit(self):
        self.assertEqual(program_checks.MAX_HOST_CHECKS, 1056)
        self.assertEqual(program_checks.MAX_PROGRAM_CHECKS, 1088)
        self.assertEqual(program_checks.MAX_CHECK_LEAVES, 131072)
        self.assertEqual(program_checks.MAX_CHECK_BYTES, 1048576)

    def test_check_scalar_and_byte_budgets_refuse_before_execution_with_split_remedy(self):
        for limit, value in (("MAX_HOST_CHECKS", 95), ("MAX_PROGRAM_CHECKS", 95),
                             ("MAX_CHECK_LEAVES", 1), ("MAX_CHECK_BYTES", 64)):
            with self.subTest(limit=limit), patch.object(program_checks, limit, value):
                with self.assertRaisesRegex(ValueError, "PROGRAM_CHECK_BUDGET:.*split.*smaller programs"):
                    canonical([cube(index) for index in range(32)])

    def test_actual_host_count_bound_refuses_instead_of_returning_a_prefix(self):
        checks = [{"kind": "object_name", "target": "$b0", "expected": "Name %d" % index}
                  for index in range(program_checks.MAX_HOST_CHECKS + 1)]
        with patch("angelo_agent.derived_checks.derive_checks", return_value=checks):
            with self.assertRaisesRegex(ValueError, "PROGRAM_CHECK_BUDGET:.*1056.*split"):
                canonical([cube(0)])

    def test_unschematic_mandatory_check_is_a_whole_program_admission_failure(self):
        invalid = {"kind": "object_name", "target": "$b0", "expected": "x" * 65}
        with patch("angelo_agent.derived_checks.derive_checks", return_value=[invalid]):
            with self.assertRaisesRegex(ValueError, "PROGRAM_CHECK_BUDGET:.*cannot be fully verified.*split"):
                canonical([cube(0)])

    def test_complete_checks_do_not_relax_global_1024_copy_budget(self):
        batch = {"kind": "batch", "target": None, "parameters": {
            "sources": [TARGET], "array": [{"step": [1, 0, 0], "count": 513}]}}
        request = canonical([batch, copy.deepcopy(batch)], bound=True)
        checks = program_checks.required_checks(request)
        self.assertEqual(sum(check["count"] for check in checks), 1024)
        extra = copy.deepcopy(batch)
        extra["parameters"]["array"][0]["count"] = 2
        with self.assertRaisesRegex(ValueError, "1024"):
            canonical([batch, copy.deepcopy(batch), extra], bound=True)

    def test_exact_compact_json_byte_boundary_preserves_unicode_escapes_and_numbers(self):
        # The size guard admits exactly the compact, non-ASCII-escaped JSON
        # representation. This exercises serialization itself, independently
        # of the additional public per-field schema restrictions.
        payloads = [
            [{"文字": "岩🪨é"}],
            [{"text": 'quotes " and slash \\ and newline\n and tab\t'}],
            [{"values": [0, -1, 1.0, -0.0, 1e-20, 1e20, 9007199254740993, True, False, None]}],
            [{"混合": ['"🪨\\', {"value": -2.5e-12, "empty": []}]}],
            [{"containers": [{}, [], [{"nested": [[], {}, []]}]] * 20}],
        ]
        for payload in payloads:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            with self.subTest(payload=payload):
                with patch.object(program_checks, "MAX_CHECK_BYTES", len(encoded)):
                    program_checks._budget(payload)
                with patch.object(program_checks, "MAX_CHECK_BYTES", len(encoded) - 1):
                    with self.assertRaisesRegex(ValueError, "PROGRAM_CHECK_BUDGET:.*UTF-8 bytes.*split"):
                        program_checks._budget(payload)

    def test_small_raw_text_does_not_bypass_the_exact_encoded_byte_limit(self):
        payload = [{"escaped": "\\" * 100}]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLess(100 + len("escaped"), 150)
        self.assertGreater(len(encoded), 150)
        with patch.object(program_checks, "MAX_CHECK_BYTES", 150):
            with self.assertRaisesRegex(ValueError, "PROGRAM_CHECK_BUDGET:.*UTF-8 bytes.*split"):
                program_checks._budget(payload)

    def test_oversized_raw_utf8_refuses_before_serializing_the_full_payload(self):
        payload = [{"name": "🪨" * 64}]
        with patch.object(program_checks, "MAX_CHECK_BYTES", 128), \
                patch.object(program_checks.json, "dumps", side_effect=AssertionError("oversized JSON was serialized")) as dump:
            with self.assertRaisesRegex(ValueError, "PROGRAM_CHECK_BUDGET:.*UTF-8.*split"):
                program_checks._budget(payload)
        dump.assert_not_called()

    def test_nonfinite_numbers_and_invalid_utf8_surrogates_refuse_actionably(self):
        payloads = [[float("nan")], [float("inf")], [-float("inf")],
                    [{"text": "\ud800"}], [{"\udfff": "text"}]]
        for payload in payloads:
            with self.subTest(payload=repr(payload)):
                with self.assertRaisesRegex(ValueError, "PROGRAM_CHECK_BUDGET:.*finite JSON:.*split"):
                    program_checks._budget(payload)


if __name__ == "__main__":
    unittest.main()
