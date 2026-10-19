# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Permanent mutation IDs outlive evictable payloads and malformed histories."""

import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from agent_runtime_fixtures import AgentRuntimeFixture
from angelo_agent.controller import Controller
from angelo_agent.invocations import (MAX_MUTATION_IDS, InvocationGuardError, check_invocation,
                                     clean_invocations, document_digest, new_invocations)
from angelo_agent.sessions import read_transcript, SessionStore

CUBE = {"name": "Cube", "size": 1, "location": [0, 0, 0]}


class InvocationRetentionTests(AgentRuntimeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.creates = 0
        self.executor.execute = Mock(side_effect=self.execute)

    def execute(self, name, arguments):
        if name == "angelo_create_cube":
            self.creates += 1
            return {"success": True, "verified": {"object_count": self.creates}, "changes": {
                "added": [{"name": arguments["name"], "type": "MESH"}], "removed": [], "modified": []}}
        return {"success": True, "objects": [], "object_count": self.creates}

    def invoke(self, identity, name="angelo_create_cube", arguments=None, c=None):
        c = c or self.c
        replies = []
        c.request_tool(identity, name, CUBE if arguments is None else arguments, replies.append)
        self.pump(c)
        return replies[0]

    def evict_payload(self):
        self.invoke("permanent-mutation")
        for index in range(256):
            self.invoke("read-" + str(index), "angelo_inspect_scene", {})
        self.assertEqual(len(self.c.state["results"]), 256)
        self.assertNotIn(self.c.state["id"] + "/permanent-mutation", self.c.state["results"])

    def test_256_observations_never_reauthorize_an_evicted_mutation_id(self):
        self.evict_payload()
        result = self.invoke("permanent-mutation")
        self.assertTrue(result["replayed"])
        self.assertTrue(result["result_omitted"])
        self.assertEqual(self.creates, 1)
        self.assertEqual(len(self.c.state["invocations"]["entries"]), 1)

    def test_changed_arguments_and_readonly_reuse_reject_after_payload_eviction(self):
        self.evict_payload()
        changed = self.invoke("permanent-mutation", arguments=dict(CUBE, name="Another cube"))
        self.assertIn("different", changed["error"])
        readonly = self.invoke("permanent-mutation", "angelo_inspect_scene", {})
        self.assertIn("different", readonly["error"])
        self.assertEqual(self.creates, 1)

    def test_restart_retains_evicted_mutation_guard(self):
        self.evict_payload()
        self.c.close()
        resumed = Controller(self.directory.name, session_id=self.c.state["id"])
        self.controllers.append(resumed)
        resumed.document, resumed.busy = self.c.document, True
        result = self.invoke("permanent-mutation", c=resumed)
        self.assertTrue(result["replayed"])
        self.assertEqual(self.creates, 1)
        changed = self.invoke("permanent-mutation", arguments=dict(CUBE, name="Another cube"), c=resumed)
        self.assertIn("different", changed["error"])

    def test_capacity_never_evicts_ids_and_context_invalidation_cannot_reset_it(self):
        self.invoke("permanent-mutation")
        entries = self.c.state["invocations"]["entries"]
        original = dict(entries)
        document = document_digest(repr(self.c.document))
        for index in range(MAX_MUTATION_IDS - len(entries)):
            entries[self.c.state["id"] + "/seed-" + str(index)] = {
                "digest": hashlib.sha256(str(index).encode()).hexdigest(), "document": document}
        result = self.invoke("capacity-overflow")
        self.assertIn("guard is full", result["error"])
        self.assertEqual(len(self.c.state["invocations"]["entries"]), MAX_MUTATION_IDS)
        for identity, record in original.items():
            self.assertEqual(self.c.state["invocations"]["entries"][identity], record)
        self.assertTrue(self.invoke("permanent-mutation")["replayed"])
        self.assertTrue(self.invoke("safe-read", "angelo_inspect_scene", {})["success"])
        self.c.busy = False
        self.c.invalidate_runtime()
        self.c.busy = True
        self.assertIn("guard is full", self.invoke("still-full")["error"])
        self.assertEqual(self.creates, 1)

    def test_legacy_migration_preserves_known_ids_and_closes_only_unknown_mutations(self):
        self.invoke("permanent-mutation")
        self.c.pump_events()
        raw = read_transcript(self.c.store.path)
        raw.pop("invocations")
        self.c.store.path.write_text(json.dumps(raw))
        resumed = Controller(self.directory.name, session_id=self.c.state["id"])
        self.controllers.append(resumed)
        resumed.document, resumed.busy = self.c.document, True
        self.assertTrue(resumed.state["invocations"]["closed"])
        self.assertTrue(self.invoke("permanent-mutation", c=resumed)["replayed"])
        self.assertIn("different", self.invoke("permanent-mutation", "angelo_inspect_scene", {}, c=resumed)["error"])
        self.assertIn("start a new chat", self.invoke("unknown-mutation", c=resumed)["error"])
        self.assertTrue(self.invoke("safe-read", "angelo_inspect_scene", {}, c=resumed)["success"])
        self.assertEqual(self.creates, 1)


class InvocationSerializationTests(unittest.TestCase):
    def test_corrupt_records_keep_known_id_blocked_and_never_serialize_nonfinite_data(self):
        key = str(uuid.uuid4()) + "/known-mutation"
        raw = {"version": 1, "closed": False, "entries": {key: {"digest": float("nan"), "document": object()}}}
        clean = clean_invocations(raw)
        self.assertTrue(clean["closed"])
        self.assertIn(key, clean["entries"])
        json.dumps(clean, allow_nan=False)
        state = {"invocations": clean}
        with self.assertRaises(InvocationGuardError):
            check_invocation(state, key, "a" * 64, "document", read_only=True)
        self.assertFalse(check_invocation(state, "safe-new-read", "a" * 64, "document", read_only=True))
        with self.assertRaises(InvocationGuardError):
            check_invocation(state, "unsafe-new-mutation", "a" * 64, "document")

    def test_unknown_versions_missing_fields_and_oversized_ids_are_closed(self):
        for raw in (None, [], {"version": True, "closed": False, "entries": {}},
                    {"version": 2, "closed": False, "entries": {}}, new_invocations() | {"prompt": "never retain"},
                    {"version": 1, "closed": False, "entries": {"x" * 2049: {"digest": "a" * 64, "document": "b" * 64}}}):
            clean = clean_invocations(raw)
            self.assertTrue(clean["closed"])
            self.assertNotIn("prompt", clean)
            json.dumps(clean, allow_nan=False)

    def test_fresh_missing_file_is_open_but_corrupt_existing_named_history_is_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = str(uuid.uuid4())
            store = SessionStore(directory, identity)
            self.assertFalse(store.load()["invocations"]["closed"])
            for raw in ("not JSON", "[]", '{"version":1,"messages":null}', '{"version":2,"messages":[]}'):
                store.path.write_text(raw)
                state = store.load()
                self.assertEqual(state["id"], identity)
                self.assertTrue(state["invocations"]["closed"])
                store.save(state)
                self.assertTrue(store.load()["invocations"]["closed"])
            with patch.object(Path, "read_text", side_effect=PermissionError("private history unreadable")):
                self.assertTrue(store.load()["invocations"]["closed"])

    def test_legacy_surrogate_document_cannot_reset_corrupt_guard_to_open(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory, str(uuid.uuid4()))
            raw = store.new()
            raw.pop("invocations")
            raw["results"]["old-mutation"] = {"invocation_digest": "a" * 64, "document": "\ud800"}
            store.path.write_text(json.dumps(raw))
            state = store.load()
            self.assertTrue(state["invocations"]["closed"])
            self.assertIn("old-mutation", state["invocations"]["entries"])
            store.save(state)
            self.assertTrue(store.load()["invocations"]["closed"])


if __name__ == "__main__":
    unittest.main()
