# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fault-injected host journals deliver truth without repeating native work."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.sessions import read_transcript  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget

CUBE = {"name": "Durability Cube", "size": 1, "location": [0, 0, 0]}


class Executor:
    def __init__(self):
        self.creates = 0
        self.failure = False

    def precondition(self, name, arguments):
        return self.creates

    def execute(self, name, arguments):
        self.creates += 1
        if self.failure:
            raise RuntimeError("Native failure after an observed change")
        return {"success": True, "verified": {"object_count": self.creates}, "scene_changed": True,
                "changes": {"added": [{"name": arguments["name"], "type": "MESH"}], "removed": [], "modified": []}}


class OutcomeDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("factory-runtime", 51))
        self.c._begin_metrics()
        self.c.busy = True
        self.executor = Executor()
        self.replies = []
        self.original_save = self.c.store.save

    def tearDown(self):
        self.c.store.save = self.original_save
        self.c.close()
        self.temp.cleanup()

    def admit(self):
        self.c.request_tool("once", "angelo_create_cube", CUBE, self.replies.append)
        self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        return next(iter(self.c.pending.values()))

    def fail_final(self, call):
        def save(state):
            if state["results"].get(call.id, {}).get("phase") == "completed":
                raise OSError("Injected storage failure; no private error text should be delivered")
            self.original_save(state)
        self.c.store.save = save

    def test_actual_success_delivers_all_waiters_and_keeps_durable_start_guard(self):
        call = self.admit()
        second = []
        self.c.request_tool("once", "angelo_create_cube", CUBE, second.append)
        call.callbacks.insert(0, lambda result: (_ for _ in ()).throw(RuntimeError("Dead transport")))
        self.fail_final(call)
        self.c.request_checkpoint = "safety-fixture"
        self.c.execute_tool(call, self.executor, self.c.document)
        self.assertEqual(self.executor.creates, 1)
        self.assertEqual(len(self.replies), 1)
        self.assertEqual(self.replies, second)
        result = self.replies[0]
        self.assertTrue(result["success"])
        self.assertEqual(result["verified"]["object_count"], 1)
        self.assertFalse(result["journal_durable"])
        self.assertEqual(result["request_checkpoint"], "safety-fixture")
        self.assertNotIn("Injected", result["journal_warning"])
        self.assertFalse(self.c.pending)
        disk = read_transcript(self.c.store.path)
        self.assertEqual(disk["results"][call.id]["phase"], "execution_started")
        self.assertIn(call.id, disk["invocations"]["entries"])
        self.assertEqual(self.c.state["results"][call.id]["phase"], "completed")
        self.assertEqual(self.c._request_metrics.snapshot()["counts"]["tool_completions"], 1)
        self.assertIn("first_verified_result_ms", self.c._request_metrics.snapshot()["phases"])
        self.c.pump_events()  # Ongoing failed storage must not disable UI pumps.
        self.assertEqual(self.c.state["messages"][-1]["status"], "completed")
        self.assertFalse(self.c.state["messages"][-1]["result"]["journal_durable"])
        self.assertTrue(self.c.persistence_warning)

    def test_ram_replay_and_restart_never_repeat_an_undurable_completed_mutation(self):
        call = self.admit()
        self.fail_final(call)
        self.c.execute_tool(call, self.executor, self.c.document)
        replay = []
        self.c.request_tool("once", "angelo_create_cube", CUBE, replay.append)
        self.assertTrue(replay[0]["replayed"])
        self.assertFalse(replay[0]["journal_durable"])
        resumed = Controller(self.temp.name)
        try:
            resumed.document, resumed.busy = self.c.document, True
            result = []
            resumed.request_tool("once", "angelo_create_cube", CUBE, result.append)
            resumed.pump_tools(self.executor, resumed.document, budget=ToolDispatchBudget(seconds=60))
            self.assertTrue(result[0]["replayed"])
            self.assertIn("error", result[0])
            self.assertFalse(resumed.pending)
            self.assertEqual(self.executor.creates, 1)
        finally:
            resumed.close()

    def test_local_acceptance_finishes_even_if_final_save_fails(self):
        call = self.admit()
        call.local = True
        self.fail_final(call)
        self.c.execute_tool(call, self.executor, self.c.document)
        self.assertFalse(self.c.busy)
        self.assertEqual(self.c.status, "Completed")
        self.assertTrue(self.replies[0]["success"])
        self.assertFalse(self.replies[0]["journal_durable"])

    def test_provider_finished_event_clears_busy_despite_persistent_save_failure(self):
        call = self.admit()
        self.fail_final(call)
        self.c.execute_tool(call, self.executor, self.c.document)
        self.c.emit("finished", {"status": "completed"})
        self.c.pump_events()
        self.assertFalse(self.c.busy)
        self.assertEqual(self.c.status, "Completed")
        self.assertTrue(self.c.persistence_warning)

    def test_actual_native_failure_is_not_promoted_to_success_by_storage_recovery(self):
        call = self.admit()
        self.fail_final(call)
        self.executor.failure = True
        self.c.execute_tool(call, self.executor, self.c.document)
        self.assertIn("error", self.replies[0])
        self.assertTrue(self.replies[0]["partial_changes_possible"])
        self.assertFalse(self.replies[0]["journal_durable"])
        self.assertEqual(self.c._request_metrics.snapshot()["counts"]["tool_failures"], 1)

    def test_failed_admission_journal_never_runs_native_work(self):
        self.c.store.save = lambda state: (_ for _ in ()).throw(OSError("Before admission"))
        self.c.request_tool("once", "angelo_create_cube", CUBE, self.replies.append)
        self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        self.assertIn("no native work", self.replies[0]["error"])
        self.assertFalse(self.c.pending)
        self.assertEqual(self.executor.creates, 0)
        self.assertIn(self.c.state["id"] + "/once", self.c.state["invocations"]["entries"])

    def test_failed_execution_start_journal_never_runs_native_work(self):
        call = self.admit()
        def save(state):
            if state["results"].get(call.id, {}).get("phase") == "execution_started":
                raise OSError("Before execution")
            self.original_save(state)
        self.c.store.save = save
        self.c.execute_tool(call, self.executor, self.c.document)
        self.assertIn("error", self.replies[0])
        self.assertEqual(self.executor.creates, 0)


if __name__ == "__main__":
    unittest.main()
