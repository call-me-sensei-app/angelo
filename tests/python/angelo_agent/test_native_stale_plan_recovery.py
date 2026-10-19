# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Both provider loops: a stale refusal that changed nothing, a read that shows what changed, a correction."""

import copy
import json
import unittest
from unittest.mock import Mock, patch

from claude_protocol_fixtures import host
from test_native_codex_plan import PlanTurnFixture
from test_native_refusal_recovery import A, B, HANDLE, TOOL, failure, snapshot
from agent_runtime_fixtures import program
from angelo_agent.providers import ClaudeProvider
from angelo_agent.refusal_codes import RefusalError, error_result
from angelo_agent.identifiers import IdentifierTable
from angelo_agent.request_fences import DeliveryOwner

LOOKUP = {"tool": "angelo_scene_object", "arguments": {"object": HANDLE, "sections": ["TRANSFORM"], "limit": 16}}


def edit(*, corrected=False):
    args = program()
    if corrected:
        args["write_set"][0]["revision"] = B  # Bound to what the read showed; no citation is needed.
    return {"tool": TOOL, "arguments": args}


def committed():
    return {"success": True, "status": "committed", "scene_changed": True,
            "checks": [{"kind": "transform", "status": "pass", "passed": True}],
            "live_revisions": {"status": "COMMITTED", "objects": [{"handle": HANDLE, "object_revision": "c" * 64}]}}


class InformedOwner:
    """Exact preconditions plus the real controller's common request barrier.

    Execution is a stand-in. SharedControllerTests (test_native_refusal_recovery)
    separately exercise actual queue/approval/commit admission in Auto bounded.
    """
    def __init__(self, controller):
        self.c = controller
        self.c._identifiers = IdentifierTable()
        self.c._identifiers.compact({"handle": HANDLE, "object_revision": A})
        self.c._identifiers.observe({"objects": [{"handle": HANDLE, "object_revision": A}]})
        self.c.recovery_barrier.observe(snapshot())
        self.calls, self.commits, self.failures = [], [], 0
        self.delivery_owners = []

    def request(self, identity, name, arguments, callback, *, delivery_owner=None):
        if delivery_owner is not None:
            if (not isinstance(delivery_owner, DeliveryOwner) or not delivery_owner.current(self.c)
                    or self.c.cancelled):
                return callback({"error": "The native delivery owner is closed", "error_code": "CANCELLED",
                                 "scene_changed": False})
            self.delivery_owners.append(delivery_owner)
        try:
            arguments = self.c.resolve_call(name, arguments)
        except RefusalError as error:
            return callback(error_result(error, scene_changed=False))
        self.calls.append((name, arguments))
        if name == TOOL and not self.failures:
            self.failures += 1
            return callback(self.c.recovery_barrier.refused(name, arguments, failure()))
        if name == "angelo_scene_object":
            return callback(self.c.recovery_barrier.read(name, snapshot(B, location=5), {
                "object": HANDLE, "object_revision": B, "sections": {"transform": {"location": [5, 0, 0]}}}))
        if name == TOOL:
            assert self.c.recovery_barrier.check(name, arguments) is False
            if arguments["write_set"][0]["revision"] != B:  # The native precondition still refuses a stale binding.
                return callback(self.c.recovery_barrier.refused(name, arguments, failure()))
            self.commits.append(arguments["write_set"][0]["revision"])
            return callback(committed())
        return callback({"success": True})


class BothLoops(PlanTurnFixture):
    def setUp(self):
        super().setUp()
        self.owner = InformedOwner(self.c)
        self.context.request_tool = self.owner.request

    def test_codex_in_turn_read_then_final_plan_can_correct(self):
        plan = lambda op: json.dumps({"reply": "", "operations": [op]})
        status = self.run_turns([("message", plan(edit()))],
                                [("tool", LOOKUP["tool"], LOOKUP["arguments"]), ("message", plan(edit(corrected=True)))])
        self.assertEqual(status, "completed")
        self.assertEqual(self.owner.commits, [B])
        self.assertEqual([name for name, _ in self.owner.calls], [TOOL, LOOKUP["tool"], TOOL])
        responses = json.dumps(self.p.rpc.responses)
        self.assertIn('before', responses)
        self.assertIn('after', responses)
        self.assertIsNone(self.c.recovery_barrier.pending)
        self.assertEqual(len(self.owner.delivery_owners), 1)
        self.assertIs(self.owner.delivery_owners[0].parent, self.context.delivery_owner)
        self.assertEqual(self.owner.delivery_owners[0].fence, self.context.request_fence)

    def test_closed_delivery_owner_admits_no_read_or_recovery_evidence(self):
        owner = DeliveryOwner(self.context.request_fence, self.context.delivery_owner)
        owner.close()
        records = copy.deepcopy(self.c.recovery_barrier.records)
        answers = []
        self.owner.request("closed-read", LOOKUP["tool"], LOOKUP["arguments"], answers.append,
                           delivery_owner=owner)
        self.assertEqual(answers[0]["error_code"], "CANCELLED")
        self.assertFalse(self.owner.calls)
        self.assertFalse(self.owner.delivery_owners)
        self.assertEqual(self.c.recovery_barrier.records, records)

    def test_claude_read_plan_then_correction_can_complete(self):
        context = host()
        context.resolve_call = self.c.resolve_call
        context.request_observation = Mock(side_effect=lambda callback: callback({"objects": []}))
        provider = ClaudeProvider(context, {})
        plans = [{"reply": "", "operations": [edit()], "verify": False},
                 {"reply": "", "operations": [LOOKUP], "verify": True},
                 {"reply": "", "operations": [edit(corrected=True)], "verify": False}]
        provider._turn = Mock(side_effect=plans)
        def native(name, arguments, **kwargs):
            result = []
            self.owner.request("claude-call", name, arguments, result.append)
            return result[0]
        provider._native_call = native
        self.assertEqual(provider.run(None, "Move the cube", observation={}), "completed")
        self.assertEqual(self.owner.commits, [B])
        self.assertEqual(provider._turn.call_count, 3)
        final_prompt = provider._turn.call_args.args[1]
        self.assertIn('"before"', final_prompt)
        self.assertIn('"after"', final_prompt)


if __name__ == "__main__":
    unittest.main()
