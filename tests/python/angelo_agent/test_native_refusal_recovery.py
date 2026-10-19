# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Structured refusal policy and the shared request barrier, without Blender."""

import ast
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from agent_runtime_fixtures import AgentRuntimeFixture, HANDLE, program
from angelo_agent.claude_outcomes import correctable_failure
from angelo_agent.identifiers import IdentifierTable
from angelo_agent.program_jobs import ProgramError
from angelo_agent.refusal_codes import CODES, RefusalError, error_result, refusal_code
from angelo_agent.request_recovery import RecoveryBarrier, citation
from angelo_agent.tool_specs import validate_call

A, B, C = "a" * 64, "b" * 64, "c" * 64
TOOL = "angelo_scene_program"


def snapshot(revision=A, location=0, opaque="same", handle=HANDLE):
    return {handle: {"handle": handle, "revision": revision, "fields": {"transform": str(location), "geometry": opaque},
                    "values": {"transform": {"location": [location, 0, 0]}}, "bytes": 512}}


def failure(code="STALE_REVISION", **kwargs):
    """A refusal whose result says it changed nothing."""
    return {"error": "a refusal whose prose is irrelevant", "error_code": code, "scene_changed": False,
            "partial_changes_possible": False, "refusal_details": {"handles": [HANDLE]}, **kwargs}


def ran(code="STALE_REVISION", **kwargs):
    """A refusal of an edit that may have run: its commit started, so partial changes are possible."""
    return {"error": "a refusal whose prose is irrelevant", "error_code": code, "partial_changes_possible": True,
            "mutation_started": True, "refusal_details": {"handles": [HANDLE]}, **kwargs}


def read(barrier, handle=HANDLE, records=None, tool="angelo_scene_object"):
    """A successful read of ``handle`` through the barrier, as the controller's read path passes it."""
    result = {"object": handle} if tool == "angelo_scene_object" else {"handle": handle}
    return barrier.read(tool, records or {}, result)


class StructuredCodes(unittest.TestCase):
    def test_unknown_and_internal_errors_never_assert_an_unchanged_scene(self):
        for error in (RuntimeError("Expected check failed"), ProgramError("unknown"),
                      ProgramError("unexpected live state", code="INTERNAL_ERROR")):
            result = error_result(error, scene_changed=False)
            self.assertNotIn("scene_changed", result)
            self.assertTrue(result["partial_changes_possible"])
            self.assertIsNone(correctable_failure([{"tool": TOOL}], [result]))

    def test_text_cannot_authorize_or_prevent_correction(self):
        self.assertIsNone(correctable_failure([{"tool": TOOL}], [failure("UNKNOWN")]))
        self.assertEqual(correctable_failure([{"tool": TOOL}], [failure("VALIDATION", error="The user declined")]),
                         "The user declined")
        for code in ("READ_ONLY", "DECLINED"):
            self.assertIsNone(correctable_failure([{"tool": TOOL}], [failure(code)]))

    def test_a_stale_refusal_that_changed_nothing_is_corrected_with_what_to_read(self):
        # Its recovery names the targets to read again; a stale refusal that may have run never qualifies.
        for code in ("STALE_SETTING", "STALE_DEPENDENCY", "STALE_REVISION", "STALE_CONTEXT"):
            with self.subTest(code=code):
                result = RecoveryBarrier().refused(TOOL, program(), failure(code))
                text = correctable_failure([{"tool": TOOL}], [result])
                self.assertTrue(text.startswith(result["error"] + "\nRecovery: "))
                self.assertIn(HANDLE, text)
                self.assertIsNone(correctable_failure([{"tool": TOOL}], [ran(code)]))

    def test_program_error_requires_static_classification_in_production(self):
        root = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        missing = []
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ProgramError":
                    value = next((item.value for item in node.keywords if item.arg == "code"), None)
                    if value is None or isinstance(value, ast.Constant) and value.value not in CODES:
                        missing.append((path.name, node.lineno))
        self.assertEqual(missing, [])

    def test_wrapper_preserves_stale_setting_details(self):
        error = ProgramError("settings changed", code="STALE_SETTING", details={"handles": [HANDLE]})
        wrapped = ProgramError("Commit wrapper: " + str(error), code=refusal_code(error), details=error.details)
        self.assertEqual(error_result(wrapped)["error_code"], "STALE_SETTING")
        self.assertEqual(error_result(wrapped)["refusal_details"], {"handles": [HANDLE]})


class DomainPolicies(unittest.TestCase):
    def test_domain_names_are_preserved_and_unknown_codes_remain_closed(self):
        from angelo_agent.action_operator_spec import OperatorError
        from angelo_agent.action_world_spec import WorldGraphError
        from angelo_agent.rna_rules import PropertyPathError
        for error, code in ((OperatorError("SELECTION", "bad selection"), "VALIDATION"),
                            (OperatorError("OPERATOR_FAILED", "bad selection"), "UNKNOWN"),
                            (WorldGraphError("COLOR_HIDDEN", "choose one"), "VALIDATION"),
                            (WorldGraphError("IMAGE_EXPIRED", "gone"), "STALE_DEPENDENCY"),
                            (WorldGraphError("VALUE_NOT_RETAINED", "ignored"), "INTERNAL_ERROR"),
                            (PropertyPathError("RNA_RANGE", "x", "too big"), "VALIDATION"),
                            (PropertyPathError("RNA_REJECTED", "x", "too big"), "UNKNOWN")):
            self.assertEqual(refusal_code(error), code)
        self.assertEqual(OperatorError("SELECTION", "x").code, "SELECTION")

    def test_recipe_bounds_and_generic_settings_remain_classified_corrections(self):
        from angelo_agent.action_recipes import _fit_scale
        from angelo_agent.generic_settings import screen_settings
        observation = {"target": HANDLE, "status": "pass", "type": "MESH", "parent_name": None,
                       "constraints_count": 0, "transforms": {"scale": [1, 1, 1]},
                       "mesh": {"dimensions_world": [1, 1, 1]},
                       "effective_linear_transform": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        with self.assertRaises(RefusalError) as caught:
            _fit_scale(HANDLE, {"dimensions": [10, 10, 10], "minimum_scale": .1, "maximum_scale": 2,
                                "maximum_scale_ratio": 2}, observation)
        self.assertEqual(refusal_code(caught.exception), "VALIDATION")
        with self.assertRaises(RefusalError) as caught:
            screen_settings({str(index): 0 for index in range(65)})
        self.assertEqual(refusal_code(caught.exception), "LIMIT_EXCEEDED")

    def test_actual_enum_rejection_requires_missing_value_proof(self):
        from types import SimpleNamespace
        from angelo_agent.rna_rules import PropertyPathError, write
        class Broken:
            def __setattr__(self, key, value):
                raise ValueError("a request-looking error is not evidence")
        prop = SimpleNamespace(type="ENUM", identifier="setting", is_enum_flag=False,
                               enum_items=[SimpleNamespace(identifier="KNOWN")])
        for value, expected in (("MISSING", "VALIDATION"), ("KNOWN", "UNKNOWN")):
            with self.assertRaises(PropertyPathError) as caught:
                write(Broken(), prop, "setting", value)
            self.assertEqual(refusal_code(caught.exception), expected)
        prop.enum_items = [SimpleNamespace(identifier=str(index)) for index in range(513)]
        with self.assertRaises(PropertyPathError) as caught:
            write(Broken(), prop, "setting", "MISSING")
        self.assertEqual(refusal_code(caught.exception), "UNKNOWN")


OTHER = HANDLE + "7"
RENDER = {"kind": "RENDER_STILL", "output": "still.png"}
BAKE = {"kind": "BAKE_TEXTURE", "target": HANDLE}
SCRIPT = {"description": "Retry", "code": "pass"}


def two_targets():
    args = program()
    args["write_set"].append({"handle": OTHER, "revision": A})
    return args


class NothingChangedTests(unittest.TestCase):
    """A refusal whose result says it changed nothing holds nothing; it says what to read and shows what changed."""

    def setUp(self):
        self.barrier = RecoveryBarrier()
        self.barrier.observe(snapshot())
        self.result = self.barrier.refused(TOOL, program(), failure())

    def test_nothing_waits_and_the_result_names_what_to_read(self):
        self.assertNotIn("recovery_required", self.result)
        recovery = self.result["recovery"]
        self.assertEqual((recovery["holding"], recovery["read"]), (False, [HANDLE]))
        self.assertIn("Nothing was changed", recovery["reason"])
        self.assertEqual(recovery["read_tools"], ["angelo_scene_object", "angelo_scene_settings"])
        for name, args in ((TOOL, program()), ("angelo_execute_python", SCRIPT), ("angelo_job_start", RENDER),
                           ("angelo_job_start", BAKE), ("angelo_set_transform", {"object": "Cube", "location": [1, 2, 3]})):
            with self.subTest(name=name, args=args):
                self.assertFalse(self.barrier.check(name, args))

    def test_native_recovery_text_is_never_overwritten(self):
        for primary in ("Inspect the original scene and checkpoint", {"action": "inspect", "targets": [HANDLE]}):
            with self.subTest(primary=primary):
                source = failure(recovery=copy.deepcopy(primary))
                result = RecoveryBarrier().refused(TOOL, program(), source)
                self.assertEqual(source["recovery"], primary)
                self.assertEqual(result["recovery"], primary)
                self.assertEqual(result["recovery_requirements"]["read"], [HANDLE])

    def test_the_read_of_a_named_target_shows_its_changed_values_and_closes_the_recovery(self):
        unrelated = self.barrier.read("angelo_scene_object", {}, {"object": OTHER})
        self.assertNotIn("recovery", unrelated)  # Nothing holds: only a read of a named target carries it.
        observed = read(self.barrier, records=snapshot(B, location=5))
        recovery = observed["recovery"]
        self.assertFalse(recovery["holding"])
        self.assertEqual(recovery["changes"], [{"handle": HANDLE, "field": "transform",
                                                "before": {"location": [0, 0, 0]}, "after": {"location": [5, 0, 0]}}])
        self.assertNotIn("unexplained", recovery)
        self.assertIsNone(self.barrier.pending)
        self.assertNotIn("recovery", read(self.barrier))

    def test_a_change_no_value_shows_is_named_as_unexplained(self):
        recovery = read(self.barrier, records=snapshot(B, location=4, opaque="changed"))["recovery"]
        self.assertEqual([row["field"] for row in recovery["changes"]], ["transform"])
        self.assertEqual(recovery["unexplained"], [{"handle": HANDLE, "fields": ["geometry"]}])
        barrier = RecoveryBarrier()
        barrier.observe(snapshot())
        barrier.refused(TOOL, program(), failure())
        recovery = read(barrier, records=snapshot(B))["recovery"]  # Only the revision moved.
        self.assertEqual((recovery["changes"], recovery["unexplained"]), ([], [{"handle": HANDLE, "fields": []}]))

    def test_a_target_without_an_earlier_capture_is_named(self):
        # Regression: the target was only ever seen in a commit result, so no prior values existed.
        barrier = RecoveryBarrier()
        barrier.refused(TOOL, program(), failure())
        recovery = read(barrier, records=snapshot(B, location=4))["recovery"]
        self.assertEqual(recovery["not_captured"], [HANDLE])
        self.assertEqual(recovery["current"], [{"handle": HANDLE, "field": "transform", "value": {"location": [4, 0, 0]}}])
        self.assertIsNone(barrier.pending)
        barrier.refused(TOOL, program(), failure())
        recovery = read(barrier)["recovery"]  # A read whose capture was over budget still counts as read.
        self.assertEqual(recovery["not_captured"], [HANDLE])

    def test_changed_settings_read_through_scene_settings_count(self):
        scene = "scn:" + "a" * 32 + ":0"
        barrier = RecoveryBarrier()
        barrier.refused(TOOL, program(), failure("STALE_SETTING", refusal_details={"handles": [scene]}))
        recovery = read(barrier, scene, tool="angelo_scene_settings")["recovery"]
        self.assertEqual((recovery["holding"], recovery["not_captured"]), (False, [scene]))
        self.assertIsNone(barrier.pending)

    def test_frame_changes_validation_and_own_commits_open_nothing(self):
        barrier = RecoveryBarrier()
        for result in (failure("FRAME_CHANGED"), failure("VALIDATION"), {"status": "committed", "success": True}):
            self.assertEqual(barrier.refused(TOOL, program(), result), result)
        self.assertIsNone(barrier.pending)

    def test_a_second_refusal_keeps_the_first_ones_targets(self):
        # The first refusal's target still shows its change when it is read.
        args = program()
        args["write_set"][0]["handle"] = OTHER
        self.barrier.refused(TOOL, args, failure(refusal_details={"handles": [OTHER]}))
        self.assertEqual(self.barrier.description()["read"], [HANDLE, OTHER])
        self.assertEqual(read(self.barrier, records=snapshot(B, location=3))["recovery"]["read"], [OTHER])

    def test_a_refusal_at_commit_preflight_whose_edit_never_applied_holds_nothing(self):
        # The native Undo bridge marks preparation callbacks before the last precondition check
        # (ProgramCommitController._preparation_result); the typed edit itself was never applied.
        note = "The typed edit was not applied. Preparation callbacks may have changed the scene; inspect it."
        preflight = {"error": "changed", "error_code": "STALE_REVISION", "partial_changes_possible": True,
                     "mutation_started": False, "note": note, "refusal_details": {"handles": [HANDLE]}}
        barrier = RecoveryBarrier()
        result = barrier.refused(TOOL, program(), preflight)
        self.assertNotIn("recovery_required", result)
        self.assertEqual((result["recovery"]["holding"], result["note"]), (False, note))
        self.assertFalse(barrier.check(TOOL, program()))
        for other in (dict(preflight, error_code="FRAME_CHANGED"), dict(preflight, error_code="CHECK_FAILED")):
            self.assertEqual(RecoveryBarrier().refused(TOOL, program(), other), other)
        # A rollback that failed, or a commit that started, still holds.
        for ran_result in (dict(preflight, rollback_error="kept"), dict(preflight, mutation_started=True)):
            barrier = RecoveryBarrier()
            self.assertIs(barrier.refused(TOOL, program(), ran_result)["recovery_required"], True)

    def test_a_cited_recovery_observation_is_accepted_and_grants_nothing(self):
        args = program()
        args["recovery_observation"] = citation("recovery:" + "9" * 32)
        self.assertFalse(self.barrier.check(TOOL, args))
        with self.assertRaises(RefusalError) as caught:
            citation("recovery:not-an-id")
        self.assertEqual(caught.exception.code, "VALIDATION")
        barrier = RecoveryBarrier()
        barrier.refused(TOOL, program(), ran())
        with self.assertRaises(RefusalError):
            barrier.check(TOOL, args)


class MayHaveRunTests(unittest.TestCase):
    """An edit that may have run holds every scene mutation until each target is read again."""

    def setUp(self):
        self.barrier = RecoveryBarrier()
        self.barrier.observe(snapshot())
        self.result = self.barrier.refused(TOOL, two_targets(), ran(refusal_details={"handles": [HANDLE]}))

    def held(self):
        for name, args in ((TOOL, program()), ("angelo_execute_python", SCRIPT), ("angelo_job_start", BAKE),
                           ("angelo_set_transform", {"object": "Cube", "location": [1, 2, 3]})):
            with self.subTest(name=name, args=args), self.assertRaises(RefusalError) as caught:
                self.barrier.check(name, args)
            self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")

    def released(self, barrier=None):
        """Edits of the step that completed the reads are refused with what changed; the next step's run."""
        barrier = barrier or self.barrier
        for _ in range(2):  # Every edit of that step, not only the first.
            with self.assertRaises(RefusalError) as caught:
                barrier.check(TOOL, program())
            self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
            self.assertIn("next step", str(caught.exception))
            self.assertIn("changes", caught.exception.details["recovery"])
        barrier.step()
        self.assertFalse(barrier.check(TOOL, program()))
        self.assertIsNone(barrier.pending)
        return caught.exception.details["recovery"]

    def test_named_targets_and_the_write_set_must_be_read(self):
        self.assertIs(self.result["recovery_required"], True)
        self.assertEqual((self.result["recovery"]["holding"], self.result["recovery"]["read"]), (True, [HANDLE, OTHER]))
        self.held()
        for name, args in (("angelo_scene_object", {}), ("angelo_memory_update", {}), ("angelo_write_file", {}),
                           ("angelo_job_start", RENDER), ("angelo_discard_preview", {})):
            self.assertFalse(self.barrier.check(name, args))  # Reads, files, memory and a render never wait.
        partial = read(self.barrier, records=snapshot(B, location=5))["recovery"]
        self.assertEqual((partial["holding"], partial["read"]), (True, [OTHER]))
        self.held()
        done = read(self.barrier, OTHER)["recovery"]
        self.assertTrue(done["holding"])
        self.assertIn("run from your next step", done["reason"])
        self.assertEqual(done["changes"][0]["after"], {"location": [5, 0, 0]})
        self.assertEqual(done["not_captured"], [OTHER])
        self.assertEqual(self.released()["changes"], done["changes"])

    def test_every_read_shows_the_hold_while_it_lasts(self):
        observed = self.barrier.annotate_read({"objects": []})
        self.assertEqual(observed, {"objects": [], "recovery": self.barrier.description()})
        self.assertTrue(observed["recovery"]["holding"])

    def test_a_read_that_finds_a_target_deleted_or_expired_counts(self):
        read(self.barrier)
        gone = {"error": "Observed object was deleted or is not in the bound scene; search again",
                "error_code": "STALE_REVISION", "refusal_details": {"handles": [OTHER]}}
        result = self.barrier.refused("angelo_scene_object", {"object": OTHER}, gone)
        self.assertEqual(result["recovery"]["deleted_or_expired"], [OTHER])
        self.released()

    def test_a_turn_observation_counts_only_the_rows_it_lists(self):
        records = {**snapshot(B, location=1), **snapshot(B, location=2, handle=OTHER)}
        observed = self.barrier.read(None, records, {"objects": [{"handle": HANDLE}], "scene_settings": {}})
        self.assertEqual(observed["recovery"]["read"], [OTHER])  # Captured, but not in what the model was sent.
        observed = self.barrier.read(None, records, {"objects": [{"handle": OTHER}]})
        self.assertNotIn("read", observed["recovery"])
        self.released()

    def test_captures_a_read_took_without_showing_them_never_count(self):
        # A search scans every object and captures each; only its listed rows were shown.
        records = {**snapshot(B, location=1), **snapshot(B, location=2, handle=OTHER)}
        observed = self.barrier.read("angelo_scene_search", records, {"objects": [{"object": OTHER}]})
        self.assertEqual(observed["recovery"]["read"], [HANDLE])
        for tool, result in (("angelo_scene_object", {"object": OTHER}), ("angelo_scene_relationships", {"object": HANDLE}),
                             ("angelo_scene_delta", {"objects": [{"object": HANDLE}]}), ("angelo_inspect_scene", {})):
            self.assertEqual(self.barrier.read(tool, records, result)["recovery"]["read"], [HANDLE])
        with self.assertRaises(RefusalError):
            self.barrier.check(TOOL, program())

    def test_a_hold_is_never_relaxed_by_a_later_refusal_that_changed_nothing(self):
        result = self.barrier.refused(TOOL, program(), failure())
        self.assertIs(result["recovery_required"], True)
        self.assertEqual(result["recovery"]["read"], [HANDLE, OTHER])
        self.held()

    def test_a_second_hold_adds_its_targets_and_rereads_a_named_one(self):
        read(self.barrier)
        third = HANDLE + "9"
        args = program()
        args["write_set"][0]["handle"] = third
        self.barrier.refused(TOOL, args, ran(refusal_details={"handles": [HANDLE]}))
        self.assertEqual(self.barrier.description()["read"], [HANDLE, OTHER, third])

    def test_an_unclassified_failure_holds_until_the_next_request(self):
        for result in (ran("INTERNAL_ERROR"), failure("UNKNOWN", scene_changed=False),
                       ran("STALE_CONTEXT", refusal_details={})):
            with self.subTest(result=result):
                barrier = RecoveryBarrier()
                args = program() if "INTERNAL_ERROR" in result.values() or "UNKNOWN" in result.values() else {
                    "mode": "APPLY", "read_set": [], "write_set": [], "operations": []}
                delivered = barrier.refused(TOOL, args, result)
                self.assertIs(delivered["recovery_required"], True)
                read(barrier)
                with self.assertRaises(RefusalError):
                    barrier.check(TOOL, program())
                barrier.clear()
                self.assertFalse(barrier.check(TOOL, program()))

    def test_classified_wrapper_of_an_unknown_cause_holds_like_its_cause(self):
        # CLEANUP_FAILED alone is not a barrier code; its UNKNOWN or INTERNAL_ERROR cause is (program_storage).
        for cause in ("UNKNOWN", "INTERNAL_ERROR"):
            with self.subTest(cause=cause):
                barrier = RecoveryBarrier()
                barrier.refused(TOOL, program(), failure("CLEANUP_FAILED", refusal_details={
                    "handles": [HANDLE], "cause_code": cause}))
                self.assertEqual(barrier.pending["code"], cause)
                read(barrier)
                with self.assertRaises(RefusalError):
                    barrier.check(TOOL, program())
        for details in ({"handles": [HANDLE], "cause_code": "FRAME_CHANGED"}, {"handles": [HANDLE]}):
            with self.subTest(details=details):
                barrier = RecoveryBarrier()
                barrier.refused(TOOL, program(), failure("CLEANUP_FAILED", refusal_details=details))
                self.assertFalse(barrier.check(TOOL, program()))

    def test_edits_in_the_same_step_as_the_clearing_read_are_refused_with_the_changes(self):
        # A Codex exec cell that reads and then edits; the read's result went to the cell, not the model.
        read(self.barrier, records=snapshot(B, location=5))
        self.barrier.step()  # A step that read only part of the targets does not count.
        read(self.barrier, OTHER)
        recovery = self.released()
        self.assertEqual(recovery["changes"][0]["after"], {"location": [5, 0, 0]})

    def test_the_refusal_has_no_side_effect_a_second_caller_could_use(self):
        # Codex builds its activity summary through resolve_call before the real admission.
        read(self.barrier)
        read(self.barrier, OTHER)
        for _ in range(3):
            with self.assertRaises(RefusalError):
                self.barrier.check(TOOL, program())
        self.assertIsNotNone(self.barrier.pending)

    def test_a_collaborators_read_clears_nothing(self):
        # A delegated read's result reaches only the collaborator.
        for tool, result in (("angelo_scene_object", {"object": HANDLE}), ("angelo_scene_object", {"object": OTHER})):
            self.assertNotIn("recovery", self.barrier.read(tool, {}, result, credit=False))
        gone = {"error": "deleted", "error_code": "STALE_REVISION", "refusal_details": {"handles": [OTHER]}}
        self.assertEqual(self.barrier.refused("angelo_scene_object", {}, gone, credit=False), gone)
        self.assertEqual(self.barrier.description()["read"], [HANDLE, OTHER])

    def test_a_preview_acceptance_holds_the_objects_its_program_wrote(self):
        # An acceptance names only its preview; the staged program's write set is where it wrote.
        barrier = RecoveryBarrier()
        barrier.refused("angelo_accept_preview", {"preview_id": "p" * 32}, ran(refusal_details={"handles": [OTHER]}),
                        program=program())
        self.assertEqual(barrier.description()["read"], [OTHER, HANDLE])

    def test_any_code_that_says_the_scene_may_have_changed_holds_its_targets(self):
        # A live check failed after writes whose rollback failed.
        for result in (ran("CHECK_FAILED", rollback_error="could not restore"),
                       dict(ran("VALIDATION"), scene_changed=True), ran("CANCELLED")):
            with self.subTest(code=result["error_code"]):
                barrier = RecoveryBarrier()
                self.assertIs(barrier.refused(TOOL, program(), result)["recovery_required"], True)
                with self.assertRaises(RefusalError):
                    barrier.check(TOOL, program())
        # Nothing changed, or a classified failure that names nothing to read: its own result stands, as before.
        for result in (failure("CHECK_FAILED"), failure("DECLINED", scene_changed=None),
                       {"error": "x", "error_code": "DECLINED"}):
            barrier = RecoveryBarrier()
            self.assertEqual(barrier.refused(TOOL, program(), result), result)
            self.assertIsNone(barrier.pending)
        barrier = RecoveryBarrier()
        result = dict(ran("VALIDATION"), refusal_details={})
        self.assertEqual(barrier.refused("angelo_set_transform", {"object": "Cube", "location": [0, 0, 1]}, result), result)

    def test_cache_and_changes_are_bounded_and_a_new_request_clears_everything(self):
        barrier = RecoveryBarrier()
        for index in range(200):
            barrier.observe(snapshot(handle=HANDLE + str(index)))
        self.assertLessEqual(len(barrier.records), 64)
        args = program()
        args["write_set"] = [{"handle": HANDLE + str(index), "revision": A} for index in range(136, 200)]
        barrier.refused(TOOL, args, ran(refusal_details={}))
        big = "x" * 4000
        for index in range(136, 200):
            row = snapshot(B, location=big, handle=HANDLE + str(index))
            barrier.read("angelo_scene_object", row, {"object": HANDLE + str(index)})
        self.assertLessEqual(len(json.dumps(barrier.description()["changes"])), 16384)
        self.released(barrier)
        self.assertIsNone(barrier.pending)
        self.barrier.clear()
        self.assertFalse(self.barrier.check(TOOL, program()))
        self.assertIsNone(self.barrier.pending)
        self.assertFalse(self.barrier.records)

    def test_the_change_budget_names_what_it_left_out(self):
        barrier = RecoveryBarrier()
        args = program()
        args["write_set"] = [{"handle": HANDLE + str(index), "revision": A} for index in range(8)]
        for index in range(8):
            barrier.observe(snapshot(handle=HANDLE + str(index)))
        barrier.refused(TOOL, args, ran(refusal_details={}))
        recovery = None
        for index in range(8):
            row = snapshot(B, location="y" * 3000, handle=HANDLE + str(index))
            recovery = barrier.read("angelo_scene_object", row, {"object": HANDLE + str(index)})["recovery"]
        self.assertLessEqual(len(json.dumps(recovery["changes"])), 16384)
        self.assertEqual(len(recovery["changes"]) + recovery["changes_omitted"], 8)


class SharedControllerTests(AgentRuntimeFixture, unittest.TestCase):
    """The controller's own admission, read path and finish (both runtimes)."""

    def refuse(self, result):
        self.c.recovery_barrier.observe(snapshot())
        answers, call = self.enqueue()
        self.c.finish_tool(call, result)
        return answers[0]

    def lookup(self, handle=HANDLE):
        answers = []
        with patch.object(self.executor, "execute", return_value={"object": handle, "object_revision": B}):
            self.c.request_tool("lookup-" + handle[-2:], "angelo_scene_object", {"object": handle}, answers.append)
            for _ in range(10):
                self.pump()
                if answers:
                    break
        return answers[0]

    def test_a_refusal_that_changed_nothing_admits_the_next_edit_script_and_job(self):
        answer = self.refuse(failure())
        self.assertNotIn("recovery_required", answer)
        self.assertEqual(answer["recovery"]["read"], [HANDLE])
        for identity, name, args in (("edit", TOOL, program()), ("script", "angelo_execute_python", SCRIPT),
                                     ("job", "angelo_job_start", BAKE)):
            answers = []
            self.c.request_tool(identity, name, args, answers.append)
            self.assertNotIn("RECOVERY_REQUIRED", [answer.get("error_code") for answer in answers])

    def test_may_have_run_holds_both_runtime_contexts_until_the_targets_are_read(self):
        from angelo_agent.routing import RuntimeContext
        self.refuse(ran())
        args = program()
        args.pop("write_set")
        for provider in ("claude_code", "codex_app_server"):
            context = RuntimeContext(self.c, provider, {}, "", "test-connection", self.c._epoch)
            with self.assertRaises(RefusalError):
                context.resolve_call(TOOL, args)
            answers = []
            context.request_tool(provider, TOOL, args, answers.append)
            self.assertEqual(answers[0]["error_code"], "RECOVERY_REQUIRED")
        self.assertFalse(self.executor.jobs)
        answers = []
        self.c.request_tool("render", "angelo_job_start", RENDER, answers.append)
        self.assertNotIn("RECOVERY_REQUIRED", [answer.get("error_code") for answer in answers])
        self.assertIn("next step", self.lookup()["recovery"]["reason"])
        answers = []
        self.c.request_tool("after-read", TOOL, program(), answers.append)
        self.assertEqual(answers[0]["error_code"], "RECOVERY_REQUIRED")
        self.assertIn("changes", answers[0]["refusal_details"]["recovery"])
        context = RuntimeContext(self.c, "codex_app_server", {}, "", "test-connection", self.c._epoch)
        context.record_model_step()  # The model's next response (provider_usage).
        answers = []
        self.c.request_tool("next-step", TOOL, program(), answers.append)
        self.assertEqual(answers, [])
        self.assertIn(self.c.state["id"] + "/next-step", self.c.pending)

    def test_already_queued_mutation_cannot_bypass_a_later_hold(self):
        self.c.recovery_barrier.observe(snapshot())
        first_answers, first = self.enqueue(identity="first")
        second_answers, second = self.enqueue(identity="second")
        self.c.finish_tool(first, ran())
        self.pump()
        self.assertEqual(second_answers[0]["error_code"], "RECOVERY_REQUIRED")
        self.assertFalse(self.executor.jobs)

    def test_after_the_reads_a_correction_runs_in_the_chats_own_approval_mode(self):
        # The cited, forced review is retired: an informed correction is approved like any other edit (Auto here).
        self.refuse(ran())
        self.lookup()
        refused = []
        self.c.request_tool("same-step", TOOL, program(), refused.append)
        self.assertEqual(refused[0]["error_code"], "RECOVERY_REQUIRED")  # The step that read: with what changed.
        self.c.recovery_barrier.step()
        answers = []
        self.c.request_tool("correction", TOOL, program(), answers.append)
        for _ in range(20):
            self.pump()
            if answers:
                break
        self.assertEqual(answers[-1]["status"], "committed")
        self.assertIsNone(self.c.recovery_barrier.pending)


if __name__ == "__main__":
    unittest.main()
