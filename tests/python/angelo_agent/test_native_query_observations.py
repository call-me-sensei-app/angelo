# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Delivered query evidence through real Codex/controller dispatch; no Blender or provider."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import providers
from angelo_agent.controller import ToolDispatchBudget
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call
from angelo_agent.native_programs import BlenderProgramBackend, ProgramError
from angelo_agent.protocol import RpcError
from angelo_agent.program_spec import validate_program
from angelo_agent.query_observations import prepare_observation
from angelo_agent.request_fences import DeliveryOwner, RequestFence
from test_native_codex_launch import ProviderFixture

H1, H2 = "scn:" + "a" * 32 + ":1", "scn:" + "a" * 32 + ":2"
R1, R2 = "1" * 64, "2" * 64


def row(handle=H1, revision=R1, **changes):
    return dict({"object": handle, "object_revision": revision, "revision_exact": True,
                 "program_revision_supported": True, "revision_limitations": []}, **changes)


def program():
    return {"mode": "APPLY", "operations": [
        {"kind": "transform", "target": "o1", "parameters": {"location": [1, 2, 3]}}]}


class DeliveredQueryTests(ProviderFixture):
    def setUp(self):
        super().setUp()
        self.table = self.c._identifiers = IdentifierTable("native-thread")
        self.result = {"objects": [row()], "observation_coverage": "OBJECTS", "snapshot_exact": True}
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result

    def precondition(self, *args):
        self.fail("A read must not need approval or a mutation precondition")

    def deliver(self, tool="angelo_scene_search", arguments=None):
        self.p._request("query", "item/tool/call", {"threadId": "native-thread", "turnId": "turn-a",
            "callId": "query", "tool": tool, "arguments": arguments or {"query": "Batch_", "coverage": "PAGE"}})
        self.c.pump_tools(self, self.c.document, budget=ToolDispatchBudget(seconds=60))

    def finalizer(self, tool="angelo_scene_search", result=None, owner=None):
        result = self.result if result is None else result
        self.table.compact(result)
        return prepare_observation(self.context, tool, result, self.table,
                                   self.p._scope.delivery_owner if owner is None else owner)

    def seed(self, handle=H1, revision=R2):
        observation = {"objects": [{"handle": handle, "object_revision": revision}]}
        self.table.compact(observation)
        self.table.observe(observation)

    def test_search_is_inferred_only_after_actual_response_write(self):
        original_pack, original_respond = providers.tool_content, self.p.rpc.respond

        def pack(*args):
            self.assertIsNone(self.table.observed(H1))
            content = original_pack(*args)
            self.assertIsNone(self.table.observed(H1), "Packing is not delivery")
            return content

        def respond(identity, payload):
            self.assertIsNone(self.table.observed(H1), "An in-progress write is not delivery")
            self.assertEqual(json.loads(payload["contentItems"][0]["text"])["objects"][0]["object_revision"], R1[:8])
            original_respond(identity, payload)

        self.p.rpc.respond = respond
        with patch.object(providers, "tool_content", pack):
            self.deliver()
        self.assertEqual(self.table.observed(H1), R1)
        self.assertEqual(expand_call("angelo_scene_program", program(), self.table)["write_set"],
                         [{"handle": H1, "revision": R1}])
        self.assertEqual([name for name, _args in self.calls], ["angelo_scene_search"])

    def test_detail_uses_its_exact_row_even_when_detail_sections_are_truncated(self):
        self.result = dict(row(), truncated=True, truncations=[{"section": "GEOMETRY"}], sections={})
        self.deliver("angelo_scene_object", {"object": H1})
        self.assertEqual(self.table.observed(H1), R1)

    def test_mutating_raw_result_during_write_cannot_substitute_delivered_hash(self):
        original = self.p.rpc.respond

        def respond(identity, payload):
            self.result["objects"][0]["object_revision"] = R2
            original(identity, payload)

        self.p.rpc.respond = respond
        self.deliver()
        self.assertEqual(self.table.observed(H1), R1)
        self.assertEqual(json.loads(self.p.rpc.responses[-1][1]["contentItems"][0]["text"])
                         ["objects"][0]["object_revision"], R1[:8])

    def test_pack_failure_does_not_observe(self):
        self.result["non_json"] = float("nan")
        self.deliver()
        self.assertFalse(self.p.rpc.responses[-1][1]["success"])
        self.assertIsNone(self.table.observed(H1))

    def test_write_errors_do_not_observe(self):
        for error in (OSError("closed pipe"), RpcError("closed RPC")):
            with self.subTest(error=type(error).__name__):
                self.p.rpc.respond = Mock(side_effect=error)
                self.deliver()
                self.assertIsNone(self.table.observed(H1))

    def test_cancel_during_write_does_not_observe(self):
        self.p.rpc.respond = lambda *_args: setattr(self.c, "cancelled", True)
        self.deliver()
        self.assertIsNone(self.table.observed(H1))

    def test_native_turn_completion_during_write_does_not_observe(self):
        self.p.rpc.respond = lambda *_args: self.p._scope.complete()
        self.deliver()
        self.assertIsNone(self.table.observed(H1))

    def test_changed_request_generation_during_write_does_not_observe(self):
        self.p.rpc.respond = lambda *_args: setattr(self.c, "_request_generation", self.c._request_generation + 1)
        self.deliver()
        self.assertIsNone(self.table.observed(H1))

    def test_replaced_table_during_write_does_not_observe_either_table(self):
        replacement = IdentifierTable("native-thread")
        self.p.rpc.respond = lambda *_args: setattr(self.c, "_identifiers", replacement)
        self.deliver()
        self.assertIsNone(self.table.observed(H1))
        self.assertIsNone(replacement.observed(H1))

    def test_table_is_captured_before_packing(self):
        original = providers.tool_content
        replacement = IdentifierTable("native-thread")

        def pack(*args):
            self.c._identifiers = replacement
            return original(*args)

        with patch.object(providers, "tool_content", pack):
            self.deliver()
        self.assertIsNone(self.table.observed(H1))
        self.assertIsNone(replacement.observed(H1))

    def test_owner_must_be_child_of_this_exact_request_with_its_fence(self):
        for owner in (DeliveryOwner(self.context.request_fence),
                      DeliveryOwner(self.context.request_fence, DeliveryOwner(self.context.request_fence)),
                      DeliveryOwner(RequestFence(self.c._epoch, -1), self.context.delivery_owner)):
            with self.subTest(owner=owner):
                self.assertIsNone(self.finalizer(owner=owner))
        delivered = self.finalizer()
        self.context.delivery_owner = DeliveryOwner(self.context.request_fence)
        delivered()
        self.assertIsNone(self.table.observed(H1))

    def test_closed_request_cannot_promote(self):
        delivered = self.finalizer()
        self.context.delivery_owner.close()
        delivered()
        self.assertIsNone(self.table.observed(H1))
        self.assertIsNone(self.finalizer())

    def test_voice_context_cannot_promote(self):
        self.context.voice = object()
        self.assertIsNone(self.finalizer())

    def test_partial_rows_clear_old_bindings_and_do_not_observe_missing_objects(self):
        for changes in ({"revision_exact": False}, {"program_revision_supported": False},
                        {"revision_exact": 1}, {"program_revision_supported": 1},
                        {"object_revision": R1[:8]}, {"object_revision": None},
                        {"revision_limitations": ["unsupported"]}, {"uncertain_reason": "BUDGET"}):
            with self.subTest(changes=changes):
                self.seed()
                self.seed(H2)
                self.result = {"objects": [dict(row(), **changes)], "truncated": True, "snapshot_exact": False}
                self.deliver()
                self.assertIsNone(self.table.observed(H1))
                self.assertEqual(self.table.observed(H2), R2)
                with self.assertRaisesRegex(IdentifierError, "no revision in your latest observation"):
                    expand_call("angelo_scene_program", program(), self.table)

    def test_exact_siblings_survive_partial_page_and_missing_object_stays_unobserved(self):
        self.result = {"objects": [row(), row(H2, R2, revision_exact=False)],
                       "truncated": True, "snapshot_exact": False}
        self.deliver()
        self.assertEqual(self.table.observed(H1), R1)
        self.assertIsNone(self.table.observed(H2))
        self.assertIsNone(self.table.observed("scn:" + "a" * 32 + ":3"))

    def test_error_and_failed_result_never_promote_or_revoke(self):
        for status in ({"error": "failed"}, {"success": False}):
            with self.subTest(status=status):
                self.seed()
                self.result = dict({"objects": [row()]}, **status)
                self.deliver()
                self.assertEqual(self.table.observed(H1), R2)

    def test_arbitrary_tool_results_and_nested_metadata_are_not_observations(self):
        for tool in ("angelo_execute_python", "angelo_read_data", "angelo_inspect_scene"):
            self.assertIsNone(self.finalizer(tool))
        self.result = {"metadata": {"objects": [row()]}, "objects": [
            {"handle": H1, "object_revision": R1}, row("o1"), row("invented")]}
        self.finalizer()()
        self.assertIsNone(self.table.observed(H1))

    def test_compacted_or_model_supplied_revisions_do_not_become_inferred_evidence(self):
        self.table.compact(self.result)
        with self.assertRaisesRegex(IdentifierError, "no revision in your latest observation"):
            expand_call("angelo_scene_program", program(), self.table)
        explicit = dict(program(), write_set=[{"handle": "o1", "revision": R2}])
        expanded = expand_call("angelo_scene_program", explicit, self.table)
        self.assertEqual(expanded["write_set"], [{"handle": H1, "revision": R2}])
        self.assertIsNone(self.table.observed(H1))

    def test_explicit_wrong_and_inferred_stale_hashes_still_fail_native_binding(self):
        self.deliver()
        inferred = expand_call("angelo_scene_program", program(), self.table)
        explicit = dict(program(), write_set=[{"handle": "o1", "revision": R2}])
        explicit = expand_call("angelo_scene_program", explicit, self.table)
        self.assertEqual(explicit["write_set"][0]["revision"], R2, "Explicit bindings must not be repaired")
        backend = BlenderProgramBackend.__new__(BlenderProgramBackend)
        obj = object()
        backend.scene = SimpleNamespace(objects=[obj])
        backend.check_context = lambda *_args: None
        backend._scene_handle = lambda: "scene"
        backend._resolve = lambda _handle: obj
        for candidate, live in ((inferred, R2), (explicit, R1)):
            with self.subTest(candidate=candidate["write_set"]):
                backend._revision = lambda *_args: live
                with patch("angelo_agent.native_programs.require_cleanup_complete"), \
                     patch("angelo_agent.native_programs.admit_input", return_value=0):
                    with self.assertRaisesRegex(ProgramError, "exact object revision changed"):
                        backend.bind(validate_program("angelo_scene_program", candidate))
        backend._revision = lambda *_args: R2
        binding = {"handles": [H1], "originals": {H1: obj}, "revisions": {H1: R1}}
        with patch("angelo_agent.native_programs.assert_supported_custom_links"):
            with self.assertRaisesRegex(ProgramError, "exact target or a mutable dependency changed"):
                backend.verify(binding)


if __name__ == "__main__":
    unittest.main()
