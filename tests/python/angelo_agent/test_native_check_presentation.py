# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Only fully accounted passing typed checks may shrink in model-facing copies.

Host ProgramJob metadata, both provider serialization paths, preserved raw
evidence and fail-closed presentation policy; no provider process or Blender.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import host  # noqa: E402
from test_native_complete_checks import (RecordingBackend, canonical, cube, maximum_material_program,
                                         material_binding_facts, settle)  # noqa: E402
from angelo_agent.claude_outcomes import (certified_completion, present_plan_checks, present_program_checks)  # noqa: E402
from angelo_agent.model_results import model_result  # noqa: E402
from angelo_agent.context_ledger import observe_committed  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.program_jobs import PreviewStore, ProgramJob, WriterLease  # noqa: E402
from angelo_agent.program_checks import resolve_obligations  # noqa: E402
from angelo_agent.providers import ClaudeProvider, tool_content  # noqa: E402


def encoded(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def typed_result(cubes=32, *, mode="APPLY", authored=()):
    arguments = canonical([cube(index) for index in range(cubes)], authored)
    arguments["mode"] = mode
    backend = RecordingBackend()
    backend.fail_target = "$never"
    previews = PreviewStore()
    job = ProgramJob(backend, arguments, owner="presentation-test", previews=previews, writer=WriterLease())
    result = settle(job)
    if mode == "APPLY":
        result = job.commit()
    else:
        previews.clear()
    if not result.get("success"):
        raise AssertionError(result)
    return {"tool": "angelo_scene_program", "arguments": arguments}, result


def missing_revision():
    return {"status": "COMMITTED", "objects": [{"handle": "scn:" + "a" * 32 + ":7", "name": "Part",
            "revision_omitted": {"reason": "BUDGET", "detail": "cooperative_post_commit_budget"}}]}


class CheckPresentationTests(unittest.TestCase):
    def setUp(self):
        self.operation, self.result = typed_result()

    def assert_full(self, tool, result):
        before = encoded(result)
        self.assertIs(present_program_checks(tool, result), result)
        self.assertEqual(encoded(result), before)

    def test_actual_host_metadata_allows_first_32_rows_and_keeps_every_other_field(self):
        result = self.result
        result.update(live_revisions={"objects": [{"handle": "h1", "object_revision": "r1"}]},
                      views=[{"id": "view-1", "path": "/private/retained.png"}], preview_id="preview-1")
        before = copy.deepcopy(result)
        self.assertEqual({key: result["check_summary"][key] for key in ("required", "checked", "passed")},
                         {"required": 96, "checked": 96, "passed": 96})
        for tool in ("angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"):
            shown = present_program_checks(tool, result)
            self.assertEqual(shown["checks"], result["checks"][:32])
            self.assertEqual(shown["check_presentation"], {
                "checked": 96, "passed": 96, "shown": 32, "omitted_passing": 64, "presentation_only": True})
            self.assertEqual({key: value for key, value in shown.items() if key not in ("checks", "check_presentation")},
                             {key: value for key, value in result.items() if key != "checks"})
            self.assertEqual(result, before)

    def test_actual_host_preview_is_presented_without_changing_retained_result(self):
        _, result = typed_result(mode="PREVIEW")
        before = encoded(result)
        shown = present_program_checks("angelo_scene_program", result)
        self.assertEqual(shown["status"], "preview")
        self.assertEqual(shown["preview_id"], result["preview_id"])
        self.assertEqual(len(shown["checks"]), 32)
        self.assertEqual(encoded(result), before)

    def test_actual_host_maximum_keeps_1088_checks_and_presents_all_32_authored_constraints(self):
        arguments = maximum_material_program()
        backend = RecordingBackend()
        backend.bind = lambda program: {"handles": [], "obligations": resolve_obligations(
            program, material_binding_facts(program))}
        backend.fail_target = "$never"
        job = ProgramJob(backend, arguments, writer=WriterLease())
        self.assertTrue(settle(job)["ready_to_commit"])
        result = job.commit()
        self.assertTrue(result["success"])
        before = encoded(result)
        shown = present_program_checks("angelo_scene_program", result)
        self.assertEqual(len(result["checks"]), 1088)
        self.assertEqual(result["check_summary"]["required"], 1088)
        self.assertEqual(shown["check_presentation"], {
            "checked": 1088, "passed": 1088, "shown": 32, "omitted_passing": 1056, "presentation_only": True})
        self.assertEqual({row["kind"] for row in shown["checks"]}, {"object_count"})
        self.assertEqual(encoded(result), before)

    def test_ordinary_results_are_byte_identical_and_gain_no_metadata(self):
        _, ordinary = typed_result(1)
        self.assertNotIn("check_summary", ordinary)
        self.assert_full("angelo_scene_program", ordinary)
        boundary = copy.deepcopy(self.result)
        boundary["checks"] = boundary["checks"][:32]
        boundary["check_summary"].update(required=32, checked=32, passed=32)
        self.assert_full("angelo_scene_program", boundary)
        controller = SimpleNamespace(short_identifiers=False)
        # Only the host's bookkeeping (timing) leaves the model's copy (model_results); the checks stay whole.
        self.assertEqual(tool_content(controller, "angelo_scene_program", ordinary)[0]["text"],
                         encoded(model_result("angelo_scene_program", ordinary)))
        self.assertEqual(json.loads(encoded(model_result("angelo_scene_program", ordinary)))["checks"], ordinary["checks"])

    def test_authoritative_tool_identity_is_required_even_for_identical_payloads(self):
        self.result["tool"] = "angelo_scene_program"
        for tool in ("angelo_execute_python", "angelo_scene_verify", "angelo_inspect_preview", "custom_tool", None):
            with self.subTest(tool=tool):
                self.assert_full(tool, self.result)

    def test_missing_or_inconsistent_host_accounting_retains_full_result(self):
        missing = copy.deepcopy(self.result)
        del missing["check_summary"]
        self.assert_full("angelo_scene_program", missing)
        for summary in (None, {}, {"required": 96, "checked": 96, "passed": 96}):
            self.assert_full("angelo_scene_program", dict(self.result, check_summary=summary))
        for key, value in (("required", 95), ("checked", 97), ("passed", 95), ("checked", True), ("passed", 96.0),
                           ("checks_digest", ""), ("checks_digest", "x" * 64), ("checks_digest", "A" * 64),
                           ("checks_digest", None)):
            result = copy.deepcopy(self.result)
            result["check_summary"][key] = value
            with self.subTest(key=key, value=value):
                self.assert_full("angelo_scene_program", result)

    def test_failures_uncertainty_and_unknown_warnings_anywhere_retain_all_rows(self):
        cases = [(("success",), False), (("final",), False), (("status",), "failed"),
                 (("replayed",), True), (("partial_changes_possible",), True), (("cleanup_pending",), True),
                 (("journal_durable",), False), (("uncertain",), True), (("future_warning",), "inspect"),
                 (("checks", -1, "passed"), False), (("checks", -1, "status"), "inconclusive"),
                 (("checks", -1, "success"), False), (("checks", -1, "checks"), []),
                 (("checks", -1, "checks", 0, "passed"), False),
                 (("checks", -1, "checks", 0, "status"), "inconclusive"),
                 (("checks", -1, "checks", 0, "new_warning"), {"reason": "unproven"}),
                 (("checks", -1, "checks", 0, "reason"), "Partial observation"),
                 (("checks", -1, "checks", 0, "inconclusive_fields"), ["location"]),
                 (("checks", -1, "checks", 0, "failure_reason"), "Read-back lost"),
                 (("checks", -1, "checks", 0, "unverified"), True),
                 (("checks", -1, "checks", 0, "journal_durable"), False),
                 (("checks", -1, "checks", 0, "replayed"), True),
                 (("checks", -1, "checks", 0, "observed"), {"details": {"truncated": True}}),
                 (("checks", -1, "checks", 0, "checks"), [{"status": "pass"}])]
        for path, value in cases:
            result = copy.deepcopy(self.result)
            target = result
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path):
                self.assert_full("angelo_scene_program", result)

    def test_authored_constraints_stay_visible_and_certification_keeps_raw_evidence(self):
        authored = [{"kind": "object_count", "minimum": 0, "maximum": 64 + index} for index in range(32)]
        operation, result = typed_result(authored=authored)
        before = encoded(result)
        summary = certified_completion([operation], [result], read_only=lambda name: False)
        self.assertIsNotNone(summary)
        shown = present_program_checks("angelo_scene_program", result)
        self.assertEqual(len(result["checks"]), 128)
        self.assertEqual({row["kind"] for row in shown["checks"]}, {"object_count"})
        self.assertEqual(encoded(result), before)
        self.assertEqual(certified_completion([operation], [result], read_only=lambda name: False), summary)
        self.assertIsNone(certified_completion([operation], [shown], read_only=lambda name: False))

    def test_plan_presentation_uses_plan_tools_and_preserves_nonprogram_payloads(self):
        raw = {"success": True, "operations": [self.result, copy.deepcopy(self.result)], "views": [{"id": "v1"}]}
        operations = [self.operation, {"tool": "angelo_execute_python", "arguments": {"code": "pass"}}]
        before = encoded(raw)
        shown = present_plan_checks(operations, raw)
        self.assertEqual(len(shown["operations"][0]["checks"]), 32)
        self.assertIs(shown["operations"][1], raw["operations"][1])
        self.assertEqual(shown["views"], raw["views"])
        self.assertEqual(encoded(raw), before)
        for bad in (dict(raw, success=False), dict(raw, future_warning="inspect"), dict(raw, replayed=True),
                    dict(raw, journal_durable=False),
                    dict(raw, operations=raw["operations"][:1])):
            self.assertIs(present_plan_checks(operations, bad), bad)
        self.assertIs(present_plan_checks([{}] * 2, raw), raw)

    def test_known_revision_budget_reason_is_retained_in_both_presentations(self):
        self.result["live_revisions"] = missing_revision()
        before = encoded(self.result)
        metadata = encoded(self.result["live_revisions"])
        direct = present_program_checks("angelo_scene_program", self.result)
        planned = present_plan_checks([self.operation], {"success": True, "operations": [self.result]})["operations"][0]
        for shown in (direct, planned):
            self.assertEqual(len(shown["checks"]), 32)
            self.assertEqual(encoded(shown["live_revisions"]), metadata)
            self.assertNotIn("object_revision", shown["live_revisions"]["objects"][0])
        self.assertEqual(encoded(self.result), before)

    def test_budget_reason_at_any_other_path_or_with_sibling_warning_keeps_full_result(self):
        for location in ("root_reason", "root_omitted", "check", "nested_check", "operation", "other_reason", "object_map", "sibling"):
            result = copy.deepcopy(self.result)
            result["live_revisions"] = missing_revision()
            if location == "root_reason":
                result["reason"] = "BUDGET"
            elif location == "root_omitted":
                result["revision_omitted"] = {"reason": "BUDGET"}
            elif location == "check":
                result["checks"][-1]["reason"] = "BUDGET"
            elif location == "nested_check":
                result["checks"][-1]["checks"][0]["observed"] = {"live_revisions": missing_revision()}
            elif location == "operation":
                result["operation_results"].append({"live_revisions": missing_revision()})
            elif location == "other_reason":
                result["live_revisions"]["objects"][0]["revision_omitted"]["reason"] = "UNSUPPORTED"
            elif location == "object_map":
                result["live_revisions"]["objects"] = {0: result["live_revisions"]["objects"][0]}
            else:
                result["live_revisions"]["objects"][0]["revision_omitted"]["future_warning"] = "Inspect"
            with self.subTest(location=location):
                self.assert_full("angelo_scene_program", result)
                aggregate = {"success": True, "operations": [result]}
                self.assertIs(present_plan_checks([self.operation], aggregate), aggregate)

    def test_plan_exception_requires_typed_tool_identity_and_cannot_apply_to_envelope(self):
        self.result["live_revisions"] = missing_revision()
        for tool in ("angelo_execute_python", "custom_tool"):
            aggregate = {"success": True, "operations": [self.result, copy.deepcopy(self.result)]}
            operations = [self.operation, {"tool": tool, "arguments": {}}]
            with self.subTest(tool=tool):
                self.assertIs(present_plan_checks(operations, aggregate), aggregate)
                self.assert_full(tool, self.result)
        envelope = {"success": True, "operations": [self.result], "live_revisions": missing_revision()}
        self.assertIs(present_plan_checks([self.operation], envelope), envelope)


class ProviderPresentationTests(unittest.TestCase):
    def test_direct_tool_serialization_shortens_only_a_verified_typed_result(self):
        _, result = typed_result()
        result["live_revisions"] = {"objects": [{"handle": "scn:" + "a" * 32 + ":7", "object_revision": "b" * 64}]}
        before = encoded(result)
        table = IdentifierTable("fixture")
        controller = SimpleNamespace(short_identifiers=True, compact_identifiers=table.compact, observe_result=Mock())
        shown = json.loads(tool_content(controller, "angelo_scene_program", result)[0]["text"])
        self.assertEqual(len(shown["checks"]), 32)
        self.assertEqual(shown["live_revisions"]["objects"][0]["handle"], "o1")
        controller.observe_result.assert_called_once_with(result)
        self.assertEqual(encoded(result), before)
        controller.short_identifiers = False
        self.assertEqual(tool_content(controller, "angelo_execute_python", result)[0]["text"],
                         encoded(model_result("angelo_execute_python", result)))
        result["checks"][-1]["passed"] = False
        self.assertEqual(tool_content(controller, "angelo_scene_program", result)[0]["text"],
                         encoded(model_result("angelo_scene_program", result)))

    def test_actual_claude_continuation_serialization_uses_shared_policy(self):
        for warn in (False, True):
            operation, result = typed_result()
            result["live_revisions"] = missing_revision()
            if warn:
                result["checks"][-1]["extra_warning"] = "Inspect this last check"
            before = encoded(result)
            context = host()
            context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
            context.compact_identifiers = Mock(side_effect=lambda value: value)
            provider = ClaudeProvider(context, {})
            provider._turn = Mock(side_effect=[
                {"reply": "Applying.", "operations": [operation], "verify": True},
                {"reply": "Read-back received.", "operations": [], "verify": False}])
            provider._native_call = Mock(return_value=result)
            with self.subTest(warn=warn):
                self.assertEqual(provider.run(None, "Build the parts", observation={}), "completed")
                self.assertEqual(provider._turn.call_count, 2)
                payload = json.loads(provider._turn.call_args.args[1].split("\n", 1)[1])
                shown = payload["result"]["operations"][0]
                self.assertEqual(len(shown["checks"]), 96 if warn else 32)
                self.assertEqual("check_presentation" in shown, not warn)
                self.assertEqual(encoded(result), before)
                self.assertEqual(len(context.compact_identifiers.call_args.args[0]["operations"][0]["checks"]),
                                 96 if warn else 32)

    def test_presented_budget_metadata_never_supplies_an_inferred_revision(self):
        _, result = typed_result()
        result["live_revisions"] = missing_revision()
        handle = result["live_revisions"]["objects"][0]["handle"]
        table = IdentifierTable("fixture")
        table.compact({"handle": handle})
        table.observe({"objects": [{"handle": handle, "object_revision": "b" * 64}]})
        self.assertEqual(table.bound_revision(handle), "b" * 64)
        controller = SimpleNamespace(short_identifiers=True, compact_identifiers=table.compact,
                                     observe_result=lambda raw: observe_committed(table, raw))
        before = encoded(result)
        shown = json.loads(tool_content(controller, "angelo_scene_program", result)[0]["text"])
        self.assertEqual(len(shown["checks"]), 32)
        self.assertEqual(shown["live_revisions"]["objects"][0]["revision_omitted"],
                         result["live_revisions"]["objects"][0]["revision_omitted"])
        self.assertIsNone(table.observed(handle), "A missing new revision must invalidate the previous binding")
        with self.assertRaisesRegex(IdentifierError, "no revision"):
            table.bound_revision(handle)
        with self.assertRaisesRegex(IdentifierError, "no revision"):
            expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [{
                "kind": "transform", "target": "o1", "parameters": {"location": [1, 2, 3]}}]}, table)
        self.assertEqual(encoded(result), before)


if __name__ == "__main__":
    unittest.main()
