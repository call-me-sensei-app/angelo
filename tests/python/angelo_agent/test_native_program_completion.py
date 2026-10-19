# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Request-local native authority survives delivery without becoming JSON trust.

The completed job fixture models the native proof API; no Blender/native execution
or provider inference is claimed. Controller durability and provider dispatch run.
"""

import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_plan, program_completion
from angelo_agent.claude_outcomes import certified_completion
from angelo_agent.claude_transport import ClaudeRequest
from angelo_agent.controller import Controller
from angelo_agent.program_checks import resolve_obligations
from angelo_agent.providers import ClaudeProvider, CodexProvider
from angelo_agent.routing import RuntimeContext, handoff
from angelo_agent.tool_specs import validate_call
from test_native_material_obligations import A, facts, material, program


def passed(bundle):
    return {"success": True, "final": True, "status": "committed", "checks": [
        {"passed": True, "status": "pass", "checks": [{"passed": True, "status": "pass",
            "kind": check["kind"], "target": check.get("target"), "observed": {"value": index}}]}
        for index, check in enumerate(bundle.checks())], "changes": {"added": [], "modified": []}}


class CompletedJob:
    def __init__(self, canonical, bundle, result):
        self.program, self.bundle, self.result = canonical, bundle, result
        self.check_results, self.disposed = copy.deepcopy(result["checks"]), False

    def completion_obligations(self):
        assert not self.disposed, "Capture must precede disposal"
        self.bundle.assert_integrity(self.program)
        return self.bundle

    def cancel(self):
        self.disposed = True
        return {}


class ProgramCompletionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = Controller(self.directory.name)
        self.host.set_document(("completion-fixture", 1))
        self.host._begin_metrics()
        self.host.busy = True
        self.context = RuntimeContext(self.host, "codex_subscription", {}, "", "", self.host._epoch)
        self.request = program([material(A)], [A])
        self.bundle = resolve_obligations(self.request, facts([A]))
        self.operation = {"tool": "angelo_scene_program", "arguments": self.request}

    def tearDown(self):
        self.host.close()
        self.directory.cleanup()

    def admit(self, callback=None, tool="angelo_scene_program", arguments=None, identity="one"):
        replies = []
        self.context.request_tool(identity, tool, arguments or self.request, callback or replies.append)
        return next(call for call in self.host.pending.values() if call.id.endswith("/" + identity)), replies

    def finish(self, call, *, altered=None, persist=True):
        actual = passed(self.bundle)
        job = CompletedJob(self.request, self.bundle, actual)
        self.host.native_jobs[call.id] = job
        result = self.host.finish_tool(call, altered(actual) if altered else actual, persist=persist)
        self.assertTrue(job.disposed)
        return result

    def summary(self, result, completion=None, operation=None):
        return certified_completion([operation or self.operation], [result], read_only=lambda _: False,
                                    completions=[completion] if completion is not None else None)

    def test_actual_job_proof_is_private_and_preserves_public_payload(self):
        call, replies = self.admit()
        result = self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        self.assertIsNotNone(completion)
        self.assertIsNone(self.summary(result), "Bound checks cannot be reconstructed from JSON")
        self.assertIn("Host checks passed", self.summary(result, completion))
        expected = dict(passed(self.bundle), native_dispatch_wait_ms=call.dispatch_wait_ms)
        self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(expected, sort_keys=True))
        self.assertNotIn(self.bundle.identity, json.dumps(self.host.state))

    def test_closed_delivery_preserves_actual_outcome_without_completion_authority(self):
        call, replies = self.admit()
        call.executing = call.mutation_started = True
        call.delivery_owner.close()
        result = self.finish(call)
        self.assertTrue(result["success"])
        self.assertIs(replies[0], result)
        self.assertEqual(program_completion.completions_for(self.context, replies), [None])
        self.assertFalse(self.context.program_completions.closed)

    def test_delivery_close_revokes_an_already_delivered_completion(self):
        call, replies = self.admit()
        result = self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        self.assertIsNotNone(self.summary(result, completion))
        call.delivery_owner.close()
        self.assertIsNone(self.summary(result, completion))
        self.assertEqual(program_completion.completions_for(self.context, replies), [None])

    def codex_delivery(self, *, rpc_error=None, pack=None, repeated=False, admit_only=False):
        provider = CodexProvider(self.context, {})
        provider._scope.begin(self.context, "thread", starting=False)
        provider._scope.bind_turn("turn")
        provider.rpc = SimpleNamespace(respond=Mock(side_effect=rpc_error))
        original = self.host.request_tool
        def dispatch(*args, **kwargs):
            original(*args, **kwargs)
            call = next(iter(self.host.pending.values()))
            if not admit_only:
                self.finish(call)
            if repeated:
                args[3](next(iter(self.context.program_completions.records.values()))[0])
        from angelo_agent import providers
        with patch.object(self.host, "request_tool", side_effect=dispatch):
            if pack is None:
                provider._request("rpc", "item/tool/call", {"threadId": "thread", "turnId": "turn",
                    "callId": "one", "tool": "angelo_scene_program", "arguments": self.request})
            else:
                with patch.object(providers, "tool_content", side_effect=pack):
                    provider._request("rpc", "item/tool/call", {"threadId": "thread", "turnId": "turn",
                        "callId": "one", "tool": "angelo_scene_program", "arguments": self.request})
        return provider

    def native_end(self, provider, status="completed", **extra):
        owner = provider._scope.delivery_owner
        provider._event("turn/completed", {"threadId": "thread", "turn": {"id": "turn", "status": status, **extra}})
        self.assertFalse(owner.current(self.host), "Native delivery must remain irreversibly closed")

    def test_normal_native_terminal_preserves_delivered_dynamic_summary(self):
        provider = self.codex_delivery()
        operation, result = provider.turn_calls()[0]
        self.native_end(provider)
        completion = program_completion.completions_for(self.context, [result])[0]
        self.assertIsNotNone(self.summary(result, completion, operation))
        provider._emit_host_summary(self.context, provider._scope.fence)
        self.host.pump_events()
        self.assertEqual(sum(bool(row.get("host_summary")) for row in self.host.state["messages"]), 1)
        self.assertEqual(provider.rpc.respond.call_count, 1)

    def test_normal_native_terminal_preserves_delivered_plan_finalization(self):
        provider = self.codex_delivery()
        self.native_end(provider)
        turns = codex_plan.PlanTurns()
        turns.message_completed("final", json.dumps({"reply": "", "operations": []}))
        self.assertEqual(codex_plan.finish(provider, turns, self.context, provider._scope.fence,
                                          "thread", "completed"), "completed")
        self.host.pump_events()
        self.assertEqual(sum(bool(row.get("host_summary")) for row in self.host.state["messages"]), 1)
        self.assertEqual(provider.rpc.respond.call_count, 1)

    def assert_native_unsealed(self, provider):
        results = [result for _operation, result in provider.turn_calls()]
        self.assertEqual(program_completion.completions_for(self.context, results), [None] * len(results))
        self.assertFalse(self.context.program_completions.sealed)
        provider._emit_host_summary(self.context, provider._scope.fence)
        self.host.pump_events()
        self.assertFalse(any(row.get("host_summary") for row in self.host.state["messages"]))

    def test_failed_native_turn_cannot_seal_delivered_result(self):
        provider = self.codex_delivery()
        self.native_end(provider, "failed")
        self.assert_native_unsealed(provider)

    def test_interrupted_native_turn_cannot_seal_delivered_result(self):
        provider = self.codex_delivery()
        self.native_end(provider, "interrupted")
        self.assert_native_unsealed(provider)

    def test_native_error_with_completed_status_cannot_seal(self):
        provider = self.codex_delivery()
        self.native_end(provider, error={"message": "Provider failed"})
        self.assert_native_unsealed(provider)

    def test_packing_refusal_cannot_seal_a_successful_native_result(self):
        provider = self.codex_delivery(pack=ValueError("Packing refused"))
        self.assertFalse(provider.rpc.respond.call_args.args[1]["success"])
        self.assertEqual(len(provider.turn_calls()), 1, "Actual outcomes still participate in correction")
        self.native_end(provider)
        self.assert_native_unsealed(provider)

    def test_failed_rpc_write_cannot_seal(self):
        provider = self.codex_delivery(rpc_error=OSError("Pipe closed"))
        self.assertEqual(provider.turn_calls(), [])
        self.assertFalse(self.context.program_completions.delivered)
        self.native_end(provider)
        self.assert_native_unsealed(provider)

    def test_close_during_packing_cannot_seal_or_write(self):
        def pack(_context, _tool, result):
            completion = self.context.program_completions.get(result)
            completion.origin.delivery_owner.close()
            return []
        provider = self.codex_delivery(pack=pack)
        provider.rpc.respond.assert_not_called()
        self.native_end(provider)
        self.assert_native_unsealed(provider)

    def test_early_terminal_and_late_atomic_outcome_cannot_seal(self):
        provider = self.codex_delivery(admit_only=True)
        call = next(iter(self.host.pending.values()))
        call.executing = call.mutation_started = True
        self.native_end(provider)
        result = self.finish(call)
        self.assertTrue(result["success"])
        provider.rpc.respond.assert_not_called()
        self.assertIsNone(self.context.program_completions.get(result))
        self.assert_native_unsealed(provider)

    def test_stop_revokes_successfully_sealed_proof(self):
        provider = self.codex_delivery()
        result = provider.turn_calls()[0][1]
        self.native_end(provider)
        self.assertIsNotNone(self.context.program_completions.get(result))
        self.host.stop()
        self.assertIsNone(self.context.program_completions.get(result))
        program_completion.seal_delivered(self.context, provider._scope.delivery_owner, provider.turn_calls())
        self.assertIsNone(self.context.program_completions.get(result))

    def test_request_finish_revokes_sealed_proof_and_duplicate_end_cannot_reopen(self):
        provider = self.codex_delivery()
        result = provider.turn_calls()[0][1]
        self.native_end(provider)
        self.context.emit("finished", {"status": "completed"})
        self.native_end(provider)
        self.assertIsNone(self.context.program_completions.get(result))
        self.assertTrue(self.context.program_completions.closed)
        self.assertFalse(self.context.program_completions.sealed)

    def test_request_replacement_invalidates_sealed_proof(self):
        provider = self.codex_delivery()
        result = provider.turn_calls()[0][1]
        self.native_end(provider)
        self.host._begin_metrics()
        self.assertIsNone(self.context.program_completions.get(result))

    def test_document_change_invalidates_sealed_proof(self):
        provider = self.codex_delivery()
        result = provider.turn_calls()[0][1]
        self.native_end(provider)
        self.host.set_document(("replacement-document", 2))
        self.assertIsNone(self.context.program_completions.get(result))

    def test_duplicate_callback_and_terminal_seal_only_once(self):
        provider = self.codex_delivery(repeated=True)
        self.native_end(provider)
        self.native_end(provider)
        self.assertEqual(provider.rpc.respond.call_count, 1)
        self.assertEqual(len(provider.turn_calls()), 1)
        self.assertEqual(len(self.context.program_completions.sealed), 1)
        result = provider.turn_calls()[0][1]
        self.assertIsNotNone(self.context.program_completions.get(result))

    def test_joined_waiters_deliver_twice_but_seal_one_actual_proof(self):
        provider = self.codex_delivery(admit_only=True)
        provider._request("rpc-two", "item/tool/call", {"threadId": "thread", "turnId": "turn",
            "callId": "one", "tool": "angelo_scene_program", "arguments": self.request})
        self.assertEqual(len(self.host.pending), 1)
        self.finish(next(iter(self.host.pending.values())))
        self.assertEqual(provider.rpc.respond.call_count, 2)
        self.assertEqual(len(provider.turn_calls()), 2)
        self.assertIs(provider.turn_calls()[0][1], provider.turn_calls()[1][1])
        self.native_end(provider)
        self.assertEqual(len(self.context.program_completions.sealed), 1)
        result = provider.turn_calls()[0][1]
        self.assertIsNotNone(self.context.program_completions.get(result))

    def test_json_replay_cannot_seal(self):
        provider = self.codex_delivery()
        self.native_end(provider)
        replay_provider = CodexProvider(self.context, {})
        replay_provider._scope.begin(self.context, "thread", starting=False)
        replay_provider._scope.bind_turn("turn")
        replay_provider.rpc = SimpleNamespace(respond=Mock())
        replay_provider._request("replay-rpc", "item/tool/call", {"threadId": "thread", "turnId": "turn",
            "callId": "one", "tool": "angelo_scene_program", "arguments": self.request})
        result = replay_provider.turn_calls()[0][1]
        self.assertTrue(result["replayed"])
        self.native_end(replay_provider)
        self.assertIsNone(self.context.program_completions.get(result))
        self.assertEqual(len(self.context.program_completions.sealed), 1)

    def test_host_refusal_supplies_no_delivery_proof(self):
        provider = CodexProvider(self.context, {})
        provider._scope.begin(self.context, "thread", starting=False)
        provider._scope.bind_turn("turn")
        provider.rpc = SimpleNamespace(respond=Mock())
        provider._request("invalid", "item/tool/call", {"threadId": "thread", "turnId": "turn",
            "callId": "invalid", "tool": "angelo_scene_program", "arguments": {"mode": "INVALID"}})
        self.assertFalse(provider.rpc.respond.call_args.args[1]["success"])
        self.assertFalse(self.host.pending)
        self.native_end(provider)
        self.assert_native_unsealed(provider)

    def test_stop_before_terminal_cannot_seal(self):
        provider = self.codex_delivery()
        self.host.stop()
        self.native_end(provider)
        self.assert_native_unsealed(provider)

    def test_closed_request_scope_before_terminal_cannot_seal(self):
        provider = self.codex_delivery()
        self.context.emit("finished", {"status": "failed"})
        self.native_end(provider)
        self.assert_native_unsealed(provider)

    def test_revoked_private_origin_before_terminal_cannot_seal(self):
        provider = self.codex_delivery()
        result = provider.turn_calls()[0][1]
        self.context.program_completions.get(result).origin.revoke()
        self.native_end(provider)
        self.assert_native_unsealed(provider)

    def test_copied_operation_identity_cannot_seal(self):
        provider = self.codex_delivery()
        operation, result = provider.turn_calls()[0]
        provider._turn_calls = [(copy.deepcopy(operation), result)]
        self.native_end(provider)
        self.assert_native_unsealed(provider)

    def test_copies_private_json_and_positive_tampering_cannot_supply_proof(self):
        call, replies = self.admit()
        result = self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        self.assertIsNone(self.summary(result, replace(completion)))
        class ForgedCompletion(program_completion.ProgramCompletion):
            def verified(self, operation, result):
                return True
        fake = ForgedCompletion(completion.origin, completion.tool, completion.arguments_digest,
                                completion.obligations, completion.results_digest, completion.status)
        self.assertIsNone(self.summary(result, fake))
        fake_origin = SimpleNamespace(current=lambda _: True, result=result, call_id="pretended")
        self.assertIsNone(self.summary(result, replace(completion, origin=fake_origin)))
        self.assertIsNone(self.summary(copy.deepcopy(result), completion))
        self.assertIsNone(self.summary(dict(result, program_completion=completion.obligations.identity)))
        changed = copy.deepcopy(result)
        changed["checks"][-1]["checks"][0]["observed"]["value"] = 999
        self.assertIsNone(self.summary(changed, completion))
        other = copy.deepcopy(self.operation)
        other["arguments"]["operations"][0]["parameters"]["name"] = "Different"
        self.assertIsNone(self.summary(result, completion, other))
        self.assertEqual(program_completion.completions_for(self.context, [copy.deepcopy(result)]), [None])

    def test_duplicate_pending_waiters_receive_proof_but_journal_replay_does_not(self):
        call, first = self.admit()
        second = []
        self.context.request_tool("one", "angelo_scene_program", self.request, second.append)
        result = self.finish(call)
        self.assertIs(first[0], second[0])
        self.assertIs(program_completion.completions_for(self.context, first)[0],
                      program_completion.completions_for(self.context, second)[0])
        replay = []
        self.context.request_tool("one", "angelo_scene_program", self.request, replay.append)
        self.assertTrue(replay[0]["replayed"])
        self.assertEqual(program_completion.completions_for(self.context, replay), [None])
        self.assertIsNone(self.summary(replay[0], program_completion.completions_for(self.context, [result])[0]))

    def test_cleanup_serialization_durability_or_actual_evidence_mismatch_cannot_mint(self):
        changes = [lambda result: dict(result, cleanup_warning=True),
                   lambda result: dict(result, staging_cleanup_pending=True),
                   lambda result: dict(result, status="preview"),
                   lambda result: dict(result, checks=result["checks"][:-1]),
                   lambda result: dict(result, invalid=object())]
        for index, alter in enumerate(changes):
            with self.subTest(index=index):
                call, replies = self.admit(identity=str(index))
                self.finish(call, altered=alter)
                self.assertEqual(program_completion.completions_for(self.context, replies), [None])
        self.assertIsNotNone(self.host.recovery_barrier.pending,
                             "The serialization failure must still block blind mutation retries")

    def test_undurable_result_cannot_mint(self):
        # An independent failure scenario starts a fresh human request. The
        # serialization case above now correctly retains its recovery barrier.
        call, replies = self.admit(identity="undurable")
        self.finish(call, persist=False)
        self.assertEqual(program_completion.completions_for(self.context, replies), [None])

    def test_result_without_a_native_job_never_mints_bound_authority(self):
        call, replies = self.admit()
        self.host.finish_tool(call, passed(self.bundle))
        self.assertEqual(program_completion.completions_for(self.context, replies), [None])
        self.assertIsNone(self.summary(replies[0]))

    def test_summary_counts_derived_predicate_rows_and_refuses_nested_guard_failure(self):
        authored = {"kind": "object_count", "minimum": 0, "maximum": 10}
        self.request = program([material(A)], [A], [authored])
        self.operation = {"tool": "angelo_scene_program", "arguments": self.request}
        self.bundle = resolve_obligations(self.request, facts([A]))
        for index, status in enumerate(("pass", "inconclusive", "fail")):
            call, replies = self.admit(identity="guard" + str(index))
            actual = passed(self.bundle)
            actual["checks"][-1]["checks"].append({"kind": "material_identity", "passed": True, "status": "pass",
                "checks": [{"kind": "selected_slot", "passed": status == "pass", "status": status}]})
            self.host.native_jobs[call.id] = CompletedJob(self.request, self.bundle, actual)
            self.host.finish_tool(call, actual)
            completion = program_completion.completions_for(self.context, replies)[0]
            if status == "pass":
                self.assertIn("(4 of 4; 3 implied by the operations)", self.summary(replies[0], completion))
            else:
                self.assertIsNone(completion)
                self.assertIsNone(self.summary(replies[0]))
        shared_leaf = {"status": "pass", "passed": True}
        self.assertTrue(program_completion.positive_rows([shared_leaf, shared_leaf]))
        cycle = {"status": "pass", "passed": True}
        cycle["checks"] = [cycle]
        self.assertFalse(program_completion.positive_rows([cycle]))

    def test_runtime_pure_program_cannot_fall_back_after_missing_or_stale_proof(self):
        from test_native_material_obligations import cube
        self.request = program([cube("cube")])
        self.bundle = resolve_obligations(self.request)
        self.operation = {"tool": "angelo_scene_program", "arguments": self.request}
        call, replies = self.admit()
        result = self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        self.assertIsNotNone(self.summary(result, completion))
        self.assertIsNotNone(self.summary(result), "Explicit standalone pure compatibility remains")
        def strict(proof):
            return certified_completion([self.operation], [result], read_only=lambda _: False, completions=[proof])
        self.assertIsNone(strict(None))
        self.host._request_generation += 1
        self.assertIsNone(strict(completion))
        self.host._request_generation -= 1
        self.context.program_completions.clear()
        self.assertIsNone(strict(completion))
        self.assertIsNone(strict(program_completion.completions_for(self.context, [result])[0]))

    def test_resolved_raw_defaults_and_duplicate_numeric_spellings_keep_private_identity(self):
        from test_native_round_savings import program as cube_program
        raw = cube_program()["arguments"]
        self.request = validate_call("angelo_scene_program", raw)
        self.bundle = resolve_obligations(self.request)
        replies, duplicate = [], []
        self.context.request_tool("spellings", "angelo_scene_program", raw, replies.append)
        self.context.request_tool("spellings", "angelo_scene_program", self.request, duplicate.append)
        call = next(iter(self.host.pending.values()))
        self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        for arguments in (raw, self.request):
            self.assertIsNotNone(self.summary(replies[0], completion,
                {"tool": "angelo_scene_program", "arguments": arguments}))
        self.assertIs(completion, program_completion.completions_for(self.context, duplicate)[0])
        altered = copy.deepcopy(raw)
        altered["operations"][0]["parameters"]["size"] = 3
        self.assertIsNone(self.summary(replies[0], completion,
            {"tool": "angelo_scene_program", "arguments": altered}))

    def test_host_resolved_alias_digest_certifies_resolved_operation_only(self):
        raw = copy.deepcopy(self.request)
        raw["operations"][0]["target"] = "o1"
        raw["write_set"][0]["handle"] = "o1"
        def resolve(_name, arguments):
            value = copy.deepcopy(arguments)
            if value["operations"][0]["target"] == "o1":
                value["operations"][0]["target"] = A
                value["write_set"][0]["handle"] = A
            return value
        with patch.object(self.host, "resolve_call", side_effect=resolve):
            replies = []
            self.context.request_tool("alias", "angelo_scene_program", raw, replies.append)
            self.finish(next(iter(self.host.pending.values())))
        completion = program_completion.completions_for(self.context, replies)[0]
        self.assertIsNotNone(self.summary(replies[0], completion))
        self.assertIsNone(self.summary(replies[0], completion,
            {"tool": "angelo_scene_program", "arguments": raw}))

    def test_failed_native_proof_integrity_still_delivers_actual_result_without_certification(self):
        call, replies = self.admit()
        result = passed(self.bundle)
        job = CompletedJob(self.request, self.bundle, result)
        job.completion_obligations = Mock(side_effect=RuntimeError("Verification bundle changed"))
        self.host.native_jobs[call.id] = job
        self.host.finish_tool(call, result)
        self.assertTrue(job.disposed)
        self.assertTrue(replies[0]["success"])
        self.assertEqual(program_completion.completions_for(self.context, replies), [None])

    def test_old_request_and_end_of_request_revoke_retained_references(self):
        call, replies = self.admit()
        result = self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        self.host._request_generation += 1
        self.assertIsNone(self.summary(result, completion))
        self.host._request_generation -= 1
        self.context.emit("finished", {"status": "completed"})
        self.assertFalse(self.context.program_completions.records)
        self.assertIsNone(self.summary(result, completion))

    def test_finished_scope_refuses_late_executing_completion_for_every_waiter(self):
        call, replies = self.admit()
        duplicate = []
        self.context.request_tool("one", "angelo_scene_program", self.request, duplicate.append)
        call.executing = True  # An already-running native operation retains honest outcome delivery.
        self.context.emit("finished", {"status": "failed"})
        self.assertFalse(self.context.cancelled, "The same fence alone does not describe request end")
        self.assertTrue(self.context.program_completions.closed)
        captured = []
        original_record = self.context.program_completions.record
        def record(result, completion):
            captured.append(completion)
            original_record(result, completion)
        with patch.object(self.context.program_completions, "record", side_effect=record):
            actual = self.finish(call)
        self.assertTrue(actual["success"])
        self.assertIs(replies[0], duplicate[0])
        self.assertEqual(len(captured), 2)
        self.assertIsNone(self.summary(actual, captured[0]))
        self.assertFalse(captured[0].origin.active)
        self.assertFalse(self.context.program_completions.records)
        self.assertEqual(program_completion.completions_for(self.context, replies + duplicate), [None, None])
        self.assertIsNone(certified_completion([self.operation], replies, read_only=lambda _: False,
                                             completions=program_completion.completions_for(self.context, replies)))
        denied = []
        self.context.request_tool("late-new-call", "angelo_scene_program", self.request, denied.append)
        self.assertIn("no longer owns", denied[0]["error"])
        self.assertFalse(self.host.pending)

    def test_new_request_gets_fresh_scope_after_old_scope_ended(self):
        old = self.context
        self.host._runtime_context = old
        old.emit("finished", {"status": "failed"})
        self.assertIs(self.host.runtime_context(), old)
        self.assertTrue(old.program_completions.closed, "begin() cannot reopen the ended scope")
        self.host._begin_metrics()  # The actual _start_turn boundary increments the request generation.
        self.context = self.host.runtime_context()
        self.assertIsNot(self.context, old)
        self.assertFalse(self.context.program_completions.closed)
        call, replies = self.admit(identity="new-request")
        actual = self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        self.assertIsNotNone(self.summary(actual, completion))
        self.assertEqual(program_completion.completions_for(old, replies), [None])
        self.assertTrue(old.program_completions.closed)

    def test_cancelled_late_completion_preserves_public_outcome_without_proof(self):
        call, replies = self.admit()
        call.executing = True
        self.host.cancelled = True
        self.context.emit("finished", {"status": "interrupted"})
        actual = self.finish(call)
        self.assertTrue(actual["success"])
        self.assertEqual(replies, [actual])
        self.assertFalse(self.context.program_completions.records)
        self.assertEqual(program_completion.completions_for(self.context, replies), [None])

    def test_close_during_large_check_digest_revokes_consumption_and_releases_result(self):
        call, replies = self.admit()
        actual = passed(self.bundle)
        actual["checks"][0]["checks"][0]["observed"]["padding"] = "x" * (1024 * 1024)
        self.host.native_jobs[call.id] = CompletedJob(self.request, self.bundle, actual)
        self.host.finish_tool(call, actual)
        result = replies[0]
        completion = program_completion.completions_for(self.context, replies)[0]
        entered, resume, summaries = threading.Event(), threading.Event(), []
        original = program_completion.checks_digest
        def digest(rows):
            entered.set()
            if not resume.wait(2):
                raise AssertionError("Digest interleaving did not resume")
            return original(rows)
        with patch.object(program_completion, "checks_digest", side_effect=digest):
            worker = threading.Thread(target=lambda: summaries.append(self.summary(result, completion)))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.context.emit("finished", {"status": "failed"})
                self.assertFalse(self.context.program_completions.records)
                self.assertIsNone(completion.origin.result)
                self.assertIsNone(completion.origin.completion)
            finally:
                resume.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(summaries, [None])
        self.assertTrue(result["success"])
        self.assertEqual(len(result["checks"][0]["checks"][0]["observed"]["padding"]), 1024 * 1024)

    def test_close_during_summary_composition_refuses_the_completed_text(self):
        from angelo_agent import claude_outcomes
        call, replies = self.admit()
        result = self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        original = claude_outcomes._check_line
        def line(row, labels):
            self.context.program_completions.clear()
            return original(row, labels)
        with patch.object(claude_outcomes, "_check_line", side_effect=line):
            self.assertIsNone(self.summary(result, completion))
        self.assertFalse(self.context.program_completions.records)

    def test_close_between_certification_and_emit_drops_host_summary_only(self):
        call, replies = self.admit()
        result = self.finish(call)
        completion = program_completion.completions_for(self.context, replies)[0]
        original = self.context.emit
        def emit(kind, data):
            if data.get("host_summary"):
                self.context.program_completions.clear()
            original(kind, data)
        with patch.object(self.context, "emit", side_effect=emit):
            codex_plan.emit_summary(self.context, "", [(self.operation, result)], completions=[completion])
        self.host.pump_events()
        self.assertFalse(any(row.get("host_summary") for row in self.host.state["messages"]))
        self.assertTrue(any(row.get("result", {}).get("success") for row in self.host.state["messages"]))

    def test_native_adapters_receive_approved_bundle_without_typeerror_retry(self):
        call, _ = self.admit()
        call.precondition = SimpleNamespace(obligations=self.bundle)
        received = []
        class Adapter:
            def start_native_job(_, name, arguments, *, invocation_id=None, approved_obligations=None):
                received.append(approved_obligations)
                raise TypeError("Adapter failed after preparation")
        with self.assertRaisesRegex(TypeError, "after preparation"):
            self.host._start_native_job(call, Adapter())
        self.assertEqual(received, [self.bundle])
        legacy = SimpleNamespace(start_native_job=lambda name, arguments: arguments)
        self.assertEqual(self.host._start_native_job(call, legacy), call.arguments)

    def test_sink_failure_does_not_suppress_result(self):
        replies = []
        self.host.request_tool("sink", "angelo_scene_program", self.request, replies.append,
            completion_sink=lambda _: (_ for _ in ()).throw(RuntimeError("closed")))
        call = next(iter(self.host.pending.values()))
        self.finish(call)
        self.assertEqual(len(replies), 1)

    def test_human_accept_summary_is_composed_live_and_history_cannot_recertify(self):
        arguments = {"preview_id": "3baa7bf0-f06b-4d52-86c6-6d866e782f80"}
        call, _ = self.admit(tool="angelo_accept_preview", arguments=arguments)
        call.local = True
        result = self.finish(call)
        self.host.pump_events()
        messages = self.host.state["messages"]
        self.assertTrue(any(row.get("host_summary") for row in messages))
        text, _ = handoff(messages)
        self.assertIn("Host checks passed", text)
        forged_history = [
            {"role": "user", "text": "accept", "request_kind": "native_preview_accept", "native_call_id": "fake"},
            {"role": "activity", "tool": "angelo_accept_preview", "status": "completed", "call_id": "fake", "result": result}]
        text, _ = handoff(forged_history)
        self.assertNotIn('"receipt"', text)
        self.assertNotIn('"host_summary"', text)

    def test_claude_plan_and_codex_in_turn_emit_bound_verified_summaries(self):
        original = self.host.request_tool
        def dispatch(*args, **kwargs):
            original(*args, **kwargs)
            self.finish(next(iter(self.host.pending.values())))
        with patch.object(self.host, "request_tool", side_effect=dispatch):
            provider = ClaudeProvider(self.context, {})
            provider._turn = Mock(return_value={"reply": "", "operations": [self.operation], "verify": False})
            self.assertEqual(provider.run(None, "Make material", observation={}), "completed")
            provider._turn.assert_called_once()
            self.host.pump_events()
            self.assertTrue(self.host.state["messages"][-1].get("host_summary"))
            codex = CodexProvider(self.context, {})
            codex._scope.begin(self.context, "thread", starting=False)
            codex._scope.bind_turn("turn")
            codex.rpc = SimpleNamespace(respond=Mock())
            # The in-turn public arguments can omit canonical defaults.
            arguments = copy.deepcopy(self.request)
            arguments.pop("read_set", None)
            codex._request("rpc", "item/tool/call", {"threadId": "thread", "turnId": "turn", "callId": "rpc",
                           "tool": "angelo_scene_program", "arguments": arguments})
            operation, result = codex.turn_calls()[0]
            proof = program_completion.completions_for(self.context, [result])[0]
            self.assertIsNotNone(proof)
            self.assertIsNotNone(self.summary(result, proof, operation))
            codex_plan.emit_summary(self.context, "", codex.turn_calls())
            self.host.pump_events()
            self.assertTrue(self.host.state["messages"][-1].get("host_summary"))

    def test_both_provider_native_calls_preserve_the_registered_result_identity(self):
        original = self.host.request_tool
        def dispatch(*args, **kwargs):
            original(*args, **kwargs)
            self.finish(next(iter(self.host.pending.values())))
        with patch.object(self.host, "request_tool", side_effect=dispatch):
            codex = CodexProvider(self.context, {})
            codex._scope.begin(self.context, "thread", starting=False)
            result = codex_plan._native_call(codex, self.context, codex._scope.fence,
                                             "angelo_scene_program", self.request)
            self.assertIsNotNone(program_completion.completions_for(self.context, [result])[0])
            claude = ClaudeProvider(self.context, {})
            result = claude._native_call("angelo_scene_program", self.request, owner=ClaudeRequest(self.context))
            self.assertIsNotNone(program_completion.completions_for(self.context, [result])[0])


if __name__ == "__main__":
    unittest.main()
