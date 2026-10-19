# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recovery authority follows real human request boundaries, not provider rounds."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime_fixtures import AgentRuntimeFixture, program
from test_native_refusal_recovery import A, B, HANDLE, TOOL, failure, ran, read, snapshot
from angelo_agent.recovery_evidence import MAX_BYTES, MAX_RECORDS
from angelo_agent.request_recovery import RecoveryBarrier


class LifecycleTests(AgentRuntimeFixture, unittest.TestCase):
    def thread(self):
        return patch("angelo_agent.controller.threading.Thread",
                     return_value=SimpleNamespace(start=lambda: None, is_alive=lambda: False))

    def conflict(self):
        self.c.recovery_barrier.observe(snapshot())
        self.c.recovery_barrier.refused(TOOL, program(), failure("INTERNAL_ERROR"))

    def send(self, evidence=None):
        self.c.busy = False
        with self.thread():
            self.c.send("Move the cube", recovery_evidence=evidence)

    def test_real_send_resets_prior_authority_and_ingests_initial_values(self):
        self.conflict()
        old_generation = self.c._request_generation
        self.send(snapshot(B, location=5))
        self.assertGreater(self.c._request_generation, old_generation)
        self.assertIsNone(self.c.recovery_barrier.pending)
        self.assertIn((HANDLE, B), self.c.recovery_barrier.records)
        self.assertFalse(self.c._initial_recovery_evidence)
        answers = []
        args = program()
        args["write_set"][0]["revision"] = B
        self.c.request_tool("fresh-request", TOOL, args, answers.append)
        self.assertEqual(answers, [])
        self.assertTrue(self.c.pending)

    def test_first_observation_can_explain_a_later_conflict(self):
        self.send(snapshot())
        answers, call = self.enqueue()
        self.c.finish_tool(call, failure())
        recovery = read(self.c.recovery_barrier, records=snapshot(B, location=5))["recovery"]
        self.assertEqual(recovery["changes"][0]["before"], {"location": [0, 0, 0]})

    def test_auto_escalation_retains_the_same_user_request_hold(self):
        self.send(snapshot())
        self.c.recovery_barrier.refused(TOOL, program(), ran())
        pending = self.c.recovery_barrier.pending
        generation = self.c._request_generation
        with self.thread():
            self.c._start_turn("Move the cube", None, user_row=False)
        self.assertGreater(self.c._request_generation, generation)
        self.assertIs(self.c.recovery_barrier.pending, pending)

    def test_voice_start_resets_authority_and_ingests_its_initial_values(self):
        self.conflict()
        self.c.busy = False
        self.c.state["provider"] = "codex_subscription"
        with self.thread(), patch("angelo_agent.voice.availability", return_value=""):
            self.c.start_voice({}, recovery_evidence=snapshot(B, location=5))
        self.assertIsNone(self.c.recovery_barrier.pending)
        self.assertIn((HANDLE, B), self.c.recovery_barrier.records)
        self.assertFalse(self.c._initial_recovery_evidence)

    def test_human_preview_acceptance_starts_a_fresh_authority_boundary(self):
        answers, call = self.enqueue(mode="PREVIEW")
        for _ in range(30):
            self.pump()
            if answers:
                break
        self.assertEqual(answers[0]["status"], "preview")
        descriptor = self.c.native_previews()[0]
        self.conflict()
        self.c.busy = False
        self.assertTrue(self.c.accept_native_preview(descriptor["preview_id"], self.executor, self.c.document,
                                                      descriptor["preview_token"]))
        self.assertIsNone(self.c.recovery_barrier.pending)
        self.assertFalse(self.c.recovery_barrier.records)


class RetentionTests(unittest.TestCase):
    """What a recovery keeps: the earlier captures it bound (within the record budget) and bounded change rows."""

    def retained(self, barrier):
        rows = {id(row): row for row in barrier.records.values()}
        if barrier.pending is not None:
            rows.update({id(row): row for row in barrier.pending["before"].values() if row is not None})
        return rows

    def test_earlier_captures_stay_within_one_record_budget_beside_the_cache(self):
        for result in (failure("STALE_CONTEXT"), ran("STALE_REVISION"), ran("INTERNAL_ERROR"), failure("UNKNOWN")):
            with self.subTest(code=result["error_code"]):
                barrier = RecoveryBarrier()
                rows = {}
                for index in range(MAX_RECORDS):
                    rows.update(snapshot(handle=HANDLE + str(index)))
                barrier.observe(rows)
                args = program()
                args["write_set"] = [{"handle": handle, "revision": A} for handle in rows]
                barrier.refused(TOOL, args, dict(result, refusal_details={"handles": list(rows)}))
                for handle in rows:
                    barrier.read("angelo_scene_object", snapshot(B, location=1, handle=handle), {"object": handle})
                    retained = self.retained(barrier)
                    self.assertLessEqual(len(retained), 2 * MAX_RECORDS)
                    self.assertLessEqual(sum(row["bytes"] for row in retained.values()), 2 * MAX_BYTES)

    def test_byte_budget_includes_every_retained_reference(self):
        barrier = RecoveryBarrier()
        rows = {}
        for index in range(16):
            row = snapshot(handle=HANDLE + str(index))
            next(iter(row.values()))["bytes"] = 16384
            rows.update(row)
        barrier.observe(rows)
        args = program()
        args["write_set"] = [{"handle": handle, "revision": A} for handle in rows]
        barrier.refused(TOOL, args, ran("STALE_CONTEXT", refusal_details={"handles": list(rows)}))
        for handle in rows:
            row = snapshot(B, location=1, handle=handle)
            next(iter(row.values()))["bytes"] = 16384
            barrier.read("angelo_scene_object", row, {"object": handle})
            self.assertLessEqual(sum(record["bytes"] for record in self.retained(barrier).values()), 2 * MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
