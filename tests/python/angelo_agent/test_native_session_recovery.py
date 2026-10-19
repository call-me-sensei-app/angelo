# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Malformed persisted rows/IDs stay readable and close mutation coverage."""

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.sessions import SessionCollection, SessionStore


class Executor:
    def __init__(self):
        self.names = []
    def execute(self, name, arguments):
        self.names.append(name)
        return {"success": True, "objects": []}


class SessionRecoveryTests(unittest.TestCase):
    def test_null_and_malformed_rows_preserve_readable_history_and_safe_observations(self):
        bad_rows = [None, "not a row", {"role": "user", "text": 7}, {"role": []},
                    {"role": "activity", "call_id": None}, {"role": "user", "references": [None]}]
        with tempfile.TemporaryDirectory() as directory:
            for bad in bad_rows:
                with self.subTest(bad=bad):
                    store = SessionStore(directory)
                    state = store.new()
                    state["messages"] = [{"role": "user", "text": "Readable project request"}, bad,
                                         {"role": "assistant", "text": "Readable actual answer"}]
                    store.path.write_text(json.dumps(state))
                    controller = Controller(directory)
                    try:
                        self.assertEqual(controller.state["messages"][0]["text"], "Readable project request")
                        self.assertEqual(controller.state["messages"][1]["text"], "Readable actual answer")
                        self.assertTrue(controller.state["invocations"]["closed"])
                        self.assertIn("could not be verified", controller.state["messages"][-1]["text"])
                        controller.set_document(("new-factory", 3))
                        controller.busy = True
                        replies = []
                        controller.request_tool("unsafe-new", "angelo_create_cube",
                            {"name": "No Mutation", "size": 1, "location": [0, 0, 0]}, replies.append)
                        self.assertIn("error", replies[0])
                        executor, observations = Executor(), []
                        controller.request_tool("safe-new", "angelo_inspect_scene", {}, observations.append)
                        controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
                        self.assertTrue(observations[0]["success"])
                        self.assertEqual(executor.names, ["angelo_inspect_scene"])
                    finally:
                        controller.close()

    def test_invalid_legacy_uuid_migrates_to_valid_closed_readable_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            state = store.new()
            state["id"] = "not-a-uuid"
            state["messages"] = [{"role": "user", "text": "Retain this legacy text"}]
            store.path.write_text(json.dumps(state))
            collection = SessionCollection(directory)
            uuid.UUID(collection.active)
            restored = SessionStore(directory, collection.active).load()
            self.assertTrue(restored["invocations"]["closed"])
            self.assertEqual(restored["messages"][0]["text"], "Retain this legacy text")
            self.assertEqual(collection.ordered()[0]["title"], "Retain this legacy text")

    def test_invalid_header_values_do_not_crash_and_do_not_reopen_native_context(self):
        invalid = {"provider": ["codex_subscription"], "native_id": {}, "connection_id": 12,
                   "model": None, "updated": float("nan"), "toolset_version": True}
        with tempfile.TemporaryDirectory() as directory:
            for key, value in invalid.items():
                with self.subTest(key=key):
                    store = SessionStore(directory)
                    state = store.new()
                    state["messages"] = [{"role": "system", "text": "Readable history"}]
                    state[key] = value
                    store.path.write_text(json.dumps(state))
                    loaded = store.load()
                    self.assertTrue(loaded["invocations"]["closed"])
                    self.assertIsNone(loaded["native_id"])
                    self.assertEqual(loaded["messages"][0]["text"], "Readable history")
                    store.save(loaded)
                    self.assertTrue(store.load()["invocations"]["closed"])

    def test_named_session_rejects_another_persisted_identity_without_changing_its_path(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = str(uuid.uuid4())
            store = SessionStore(directory, expected)
            state = store.new()
            state["id"] = str(uuid.uuid4())
            store.path.write_text(json.dumps(state))
            loaded = store.load()
            self.assertEqual(loaded["id"], expected)
            self.assertTrue(loaded["invocations"]["closed"])

    def test_missing_identity_cannot_reopen_a_new_namespace_over_existing_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            state = store.new()
            old_id = state.pop("id")
            state["messages"] = [{"role": "user", "text": "Keep readable history"}]
            key = old_id + "/already-admitted"
            state["invocations"]["entries"][key] = {"digest": "a" * 64, "document": "b" * 64}
            store.path.write_text(json.dumps(state))
            loaded = store.load()
            self.assertTrue(loaded["invocations"]["closed"])
            self.assertIn(key, loaded["invocations"]["entries"])
            self.assertEqual(loaded["messages"][0]["text"], "Keep readable history")

    def test_mixed_collection_index_keeps_valid_sessions_and_safe_sort_scalars(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory, str(uuid.uuid4()))
            store.save(store.new())
            index = {"version": 1, "active": store.session_id, "sessions": [None, {"id": "bad"},
                {"id": store.session_id, "title": [], "provider": {}, "updated": "not a timestamp"}]}
            (Path(directory) / "conversations.json").write_text(json.dumps(index))
            collection = SessionCollection(directory)
            self.assertEqual(collection.active, store.session_id)
            self.assertEqual(collection.ordered(), [{"id": store.session_id, "title": "Recovered chat",
                                                    "provider": "codex_subscription", "updated": 0}])

    def test_restart_with_pending_or_partial_recovery_revokes_old_native_resume_and_retains_safety_link(self):
        with tempfile.TemporaryDirectory() as directory:
            for status in ("pending", "partial"):
                with self.subTest(status=status):
                    store = SessionStore(directory)
                    state = store.new()
                    checkpoint = str(uuid.uuid4())
                    state["native_id"] = "old-before-recovery-thread"
                    state["runtime_sessions"] = {"codex_subscription": {"native_id": state["native_id"]}}
                    state["messages"] = [{"role": "system", "text": "Recovery outcome needs inspection",
                                          "restore_state": status, "restore_checkpoint": checkpoint}]
                    store.save(state)
                    loaded = store.load()
                    self.assertIsNone(loaded["native_id"])
                    self.assertFalse(loaded["runtime_sessions"])
                    self.assertEqual(loaded["messages"][0]["restore_checkpoint"], checkpoint)
                    self.assertTrue(loaded["task_memory"]["stale"])
                    self.assertIn("fresh native context", loaded["messages"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
