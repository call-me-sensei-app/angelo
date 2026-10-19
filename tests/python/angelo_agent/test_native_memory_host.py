# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import copy
import hashlib
import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.memory_host import execute, precondition
from angelo_agent.memory_spec import MEMORY_NAMES
from angelo_agent.sessions import SessionStore
from angelo_agent.studio_state import StudioLibrary
from angelo_agent.task_memory import read_memory
from angelo_agent.tool_specs import BOUNDED_MUTATIONS, validate_call

DOC = ("memory-host-fixture", 17)


def digest(tool, arguments):
    payload = json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True,
                         ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class MemoryHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.controller = Controller(self.directory)
        self.controller.set_document(DOC)
        self.controller.busy = True
        self.callbacks = []

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    def request(self, patch_value, suffix=None):
        revision = read_memory(self.controller.state, DOC)["revision"]
        args = {"expected_revision": revision, "patch": patch_value}
        suffix = suffix or str(uuid.uuid4())
        self.controller.request_tool(suffix, "angelo_memory_update", args, self.callbacks.append)
        call = self.controller.pending[self.controller.state["id"] + "/" + suffix]
        return call, args

    def approve(self, call, args):
        call.precondition = precondition(self.controller, call.name, args)
        call.approval = True
        self.controller._journal_tool(call, "execution_started")
        call.executing = True

    def attachment(self, name="source.txt", content=b"Actual user supplied data", role="user", current=False):
        source = self.directory / name
        source.write_bytes(content)
        record = self.controller.references.import_images([str(source)])[0]
        if current:
            self.controller.turn_attachments = [record]
        else:
            self.controller.state["messages"].append({"role": role, "text": "Reference", "references": [record]})
        return record

    def ledger(self, tool, args, result, recorded_name=None, **overrides):
        args = validate_call(tool, args)
        recorded_name = recorded_name or tool  # A chat saved before the rename recorded the pre-rename name.
        identity = self.controller.state["id"] + "/receipt:" + str(uuid.uuid4())
        record = {"document": repr(DOC), "phase": "completed", "invocation_digest": digest(recorded_name, args),
                  "result": result}
        record.update(overrides)
        self.controller.state["results"][identity] = record
        self.controller.state["messages"].append({"role": "activity", "call_id": identity, "tool": recorded_name,
            "arguments": copy.deepcopy(args), "result": copy.deepcopy(result), "status": "completed"})
        return identity

    def asset(self, kind="IMAGE"):
        library = StudioLibrary(self.directory / "fixture-library")
        connection = str(uuid.uuid4())
        asset = library.add(kind, "Owned fixture asset", "Fixture model", connection, status="ready")
        name = "image.png" if kind == "IMAGE" else "model.glb"
        (library.result_directory(asset["id"]) / name).write_bytes(b"local fixture output")
        asset.update(files=[name], model_id="fixture-model")
        library.update(asset)
        self.controller.media_service = SimpleNamespace(library=library)
        return library, asset, connection

    def test_registered_tool_contract_and_read_has_no_state_or_undo_effect(self):
        self.assertTrue(MEMORY_NAMES.isdisjoint(BOUNDED_MUTATIONS))
        before = copy.deepcopy(self.controller.state)
        memory = execute(self.controller, "angelo_memory_read", {})
        self.assertEqual(self.controller.state, before)
        self.assertEqual(memory["authority"], "none")
        self.assertNotIn("changes", memory)
        self.assertNotIn("undoable", memory)
        self.assertIsNone(precondition(self.controller, "angelo_memory_read", {}))

    def test_exact_live_manual_approval_updates_under_lock_and_persists(self):
        call, args = self.request({"objective": "Measured task", "constraints": ["Keep original UVs"]})
        before = copy.deepcopy(self.controller.state["task_memory"])
        signature = precondition(self.controller, call.name, args)
        self.assertEqual(self.controller.state["task_memory"], before)
        self.approve(call, args)
        self.assertEqual(call.precondition, signature)
        response = execute(self.controller, call.name, args)
        self.assertEqual(set(response), {"success", "memory"})
        self.assertTrue(response["success"])
        self.assertEqual(response["memory"]["brief"]["objective"], "Measured task")
        self.assertEqual(response["memory"]["provenance"]["objective"]["id"], call.id)
        self.assertEqual(response["memory"]["authority"], "none")
        persisted = SessionStore(self.directory).load()
        self.assertEqual(persisted["task_memory"]["brief"]["constraints"], ["Keep original UVs"])

    def test_controller_read_then_approved_write_does_not_recursively_advance_revision(self):
        controller = self.controller

        class HostExecutor:
            def precondition(self, name, args):
                return precondition(controller, name, args)

            def execute(self, name, args):
                return execute(controller, name, args)

        before = copy.deepcopy(controller.state["task_memory"])
        received = []
        controller.request_tool("memory-read", "angelo_memory_read", {}, received.append)
        controller.pump_tools(HostExecutor(), DOC, budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(controller.state["task_memory"], before)
        revision = received[-1]["revision"]
        call, args = self.request({"objective": "A real read then exact write"})
        self.assertEqual(args["expected_revision"], revision)
        controller.pump_tools(HostExecutor(), DOC, budget=ToolDispatchBudget(seconds=60))
        self.assertTrue(call.approval)
        controller.execute_tool(call, HostExecutor(), DOC)
        self.assertNotIn(call.id, controller.pending)
        response = self.callbacks[-1]
        self.assertTrue(response["success"])
        self.assertEqual(controller.state["task_memory"]["revision"], response["memory"]["revision"])
        self.assertEqual(controller.state["task_memory"]["revision"], revision + 1)

    def test_missing_queued_auto_expired_wrong_turn_and_ambiguous_calls_are_not_permission(self):
        call, args = self.request({"objective": "Cannot self-approve"})
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, args)
        self.approve(call, args)
        for field, bad in [("approval", False), ("executing", False), ("automatic", True),
                           ("document", ("other", 3)), ("epoch", 99), ("generation", 99), ("expires", 0)]:
            original = getattr(call, field)
            setattr(call, field, bad)
            before = copy.deepcopy(self.controller.state["task_memory"])
            with self.subTest(field=field), self.assertRaises(ValueError):
                execute(self.controller, call.name, args)
            self.assertEqual(self.controller.state["task_memory"], before)
            setattr(call, field, original)
        other, other_args = self.request(args["patch"])
        self.approve(other, other_args)
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, args)
        self.controller.pending.clear()
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, args)

    def test_digest_and_execution_journal_cannot_be_substituted(self):
        call, args = self.request({"objective": "Exact accepted note"})
        self.approve(call, args)
        changed = copy.deepcopy(args)
        changed["patch"]["objective"] = "Different hidden action"
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, changed)
        call.arguments = changed
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, changed)
        call.arguments = args
        self.controller.state["results"][call.id]["phase"] = "queued"
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, args)
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, dict(args, provenance={"approved": True}))

    def test_revision_race_read_only_and_save_failure_leave_memory_unchanged(self):
        call, args = self.request({"objective": "Reviewed note"})
        self.approve(call, args)
        self.controller.state["task_memory"]["revision"] += 1
        before = copy.deepcopy(self.controller.state["task_memory"])
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, args)
        self.assertEqual(self.controller.state["task_memory"], before)
        self.controller.state["task_memory"]["revision"] -= 1
        self.controller.state["options"]["approval"] = "READ_ONLY"
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, args)
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        before = copy.deepcopy(self.controller.state)
        with patch.object(self.controller, "save", side_effect=OSError("private path must not be printed")):
            with self.assertRaisesRegex(ValueError, "could not be saved"):
                execute(self.controller, call.name, args)
        self.assertEqual(self.controller.state, before)

    def test_known_runtime_credential_is_never_saved_or_returned(self):
        self.controller.api_key = "opaque-runtime-credential-that-is-not-a-public-note"
        call, args = self.request({"objective": "Store " + self.controller.api_key})
        with self.assertRaisesRegex(ValueError, "runtime credential"):
            precondition(self.controller, call.name, args)
        call.approval, call.executing = True, True
        self.controller._journal_tool(call, "execution_started")
        with self.assertRaisesRegex(ValueError, "runtime credential"):
            execute(self.controller, call.name, args)
        self.controller.state["task_memory"]["brief"]["objective"] = self.controller.api_key
        memory = execute(self.controller, "angelo_memory_read", {})
        self.assertNotIn(self.controller.api_key, json.dumps(memory))
        self.assertEqual(memory["brief"]["objective"], "[REDACTED]")

    def test_only_real_user_attachments_or_current_user_turn_are_admitted(self):
        for current in (False, True):
            record = self.attachment("current.txt" if current else "history.txt", current=current)
            entry = {"source": "ATTACHMENT", "id": record["id"], "label": "Actually attached"}
            call, args = self.request({"references": [entry]})
            self.approve(call, args)
            response = execute(self.controller, call.name, args)
            admitted = response["memory"]["references"][0]
            self.assertEqual(admitted["provenance"]["kind"], "user_message")
            self.assertNotIn("path", admitted)
        observation = self.attachment("observation.txt", role="assistant")
        call, args = self.request({"references": [{"source": "ATTACHMENT", "id": observation["id"], "label": "Not user"}]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)

    def test_deleted_changed_or_mismatched_attachment_cannot_reuse_approval(self):
        record = self.attachment()
        call, args = self.request({"references": [{"source": "ATTACHMENT", "id": record["id"], "label": "Selected"}]})
        self.approve(call, args)
        path = Path(record["path"])
        path.write_bytes(b"Changed after the exact reference review")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            execute(self.controller, call.name, args)
        path.unlink()
        with self.assertRaises(ValueError):
            execute(self.controller, call.name, args)

    def test_literal_user_url_only_no_fetch_prefix_match_or_assistant_authority(self):
        url = "https://example.test/reference.png"
        entry = {"source": "URL", "id": url, "label": "Literal selected URL"}
        self.controller.state["messages"].append({"role": "assistant", "text": url})
        self.controller.state["messages"].append({"role": "user", "text": url + ".unapproved"})
        call, args = self.request({"references": [entry]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)
        self.controller.state["messages"].append({"role": "user", "text": "Use this [image](" + url + ")"})
        self.approve(call, args)
        response = execute(self.controller, call.name, args)
        self.assertEqual(response["memory"]["references"][0]["provenance"]["kind"], "user_message")
        private = "https://127.0.0.1/private"
        self.controller.state["messages"].append({"role": "user", "text": private})
        call, args = self.request({"references": [{"source": "URL", "id": private, "label": "Local"}]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)

    def test_workspace_requires_real_matching_attachment_or_approved_file_receipt(self):
        path = self.controller.workspace / "assets" / "source.txt"
        path.parent.mkdir()
        path.write_text("User contents")
        entry = {"source": "WORKSPACE", "id": "assets/source.txt", "label": "Project file"}
        call, args = self.request({"assets": [entry]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)
        self.attachment("source.txt", b"Different bytes with same name")
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)
        self.attachment("source.txt", b"User contents")
        self.approve(call, args)
        response = execute(self.controller, call.name, args)
        self.assertEqual(response["memory"]["assets"][0]["provenance"]["kind"], "user_message")
        written = self.controller.workspace / "written.txt"
        content = "Created by an individually approved file tool"
        written.write_text(content)
        origin = self.ledger("angelo_write_file", {"path": "written.txt", "content": content},
            {"success": True, "path": "written.txt", "bytes": len(content), "journaled": True})
        call, args = self.request({"assets": [{"source": "WORKSPACE", "id": "written.txt", "label": "Approved write"}]})
        self.approve(call, args)
        response = execute(self.controller, call.name, args)
        self.assertEqual(response["memory"]["assets"][0]["provenance"]["id"], origin)
        written.write_text("Different current file")
        call, args = self.request({"assets": [{"source": "WORKSPACE", "id": "written.txt", "label": "Changed"}]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)

    def test_file_read_only_receipt_forged_digest_and_symlink_escape_are_not_approval(self):
        path = self.controller.workspace / "read.txt"
        path.write_text("Data")
        self.ledger("angelo_read_file", {"path": "read.txt", "start": 0, "length": 4}, {"path": "read.txt", "text": "Data"})
        self.ledger("angelo_write_file", {"path": "read.txt", "content": "Data"},
            {"success": True, "path": "read.txt", "bytes": 4, "journaled": True}, invocation_digest="model-proof")
        call, args = self.request({"assets": [{"source": "WORKSPACE", "id": "read.txt", "label": "Unapproved read"}]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)
        (self.controller.workspace / "outside.txt").symlink_to(self.directory / "outside.txt")
        (self.directory / "outside.txt").write_text("Outside project")
        call, args = self.request({"assets": [{"source": "WORKSPACE", "id": "outside.txt", "label": "Escape"}]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)

    def test_unrelated_library_asset_or_model_approved_boolean_is_not_admitted(self):
        library, asset, connection = self.asset()
        entry = {"source": "ASSET", "id": asset["id"], "label": "Existing unrelated output"}
        call, args = self.request({"assets": [entry]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)
        origin = self.ledger("angelo_studio_asset", {"asset_id": asset["id"], "preview": False}, dict(asset, approved=True))
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)
        self.assertIn(origin, self.controller.state["results"])
        self.ledger("angelo_studio_generate", {"connection_id": connection, "model": "fixture-model", "prompt": "Fixture"},
                    dict(asset, approved=True), invocation_digest="not-a-real-invocation")
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)

    def test_completed_approved_generation_and_import_prove_owned_current_asset(self):
        library, asset, connection = self.asset()
        origin = self.ledger("angelo_studio_generate", {"connection_id": connection, "model": "fixture-model", "prompt": "Fixture"}, dict(asset))
        entry = {"source": "ASSET", "id": asset["id"], "label": "Owned approved output"}
        call, args = self.request({"assets": [entry]})
        self.approve(call, args)
        response = execute(self.controller, call.name, args)
        self.assertEqual(response["memory"]["assets"][0]["provenance"]["id"], origin)
        self.assertNotIn(str(library.directory), json.dumps(response))
        library.checked_file(asset, asset["files"][0]).unlink()
        call, args = self.request({"assets": [entry]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)
        library, asset, connection = self.asset("3D")
        # Stat-only library metadata must preserve Studio's model limit rather
        # than inherit the smaller workspace text-read limit.
        with library.checked_file(asset, asset["files"][0]).open("r+b") as handle:
            handle.truncate(25 * 1024 * 1024)
        origin = self.ledger("angelo_studio_import_3d", {"asset_id": asset["id"]}, {"success": True, "objects": ["Imported"]})
        call, args = self.request({"assets": [{"source": "ASSET", "id": asset["id"], "label": "Approved scene import"}]})
        self.approve(call, args)
        response = execute(self.controller, call.name, args)
        self.assertEqual(response["memory"]["assets"][0]["provenance"]["id"], origin)

    def test_a_receipt_recorded_under_the_pre_rename_tool_name_still_proves_the_asset(self):
        library, asset, connection = self.asset()
        args = {"connection_id": connection, "model": "fixture-model", "prompt": "Fixture"}
        origin = self.ledger("angelo_studio_generate", args, dict(asset), recorded_name="toonlab_studio_generate")
        entry = {"source": "ASSET", "id": asset["id"], "label": "Owned approved output"}
        call, request = self.request({"assets": [entry]})
        self.approve(call, request)
        self.assertEqual(execute(self.controller, call.name, request)["memory"]["assets"][0]["provenance"]["id"], origin)
        # The recorded name is part of the digest: a receipt claiming the other name proves nothing.
        library, asset, connection = self.asset()
        args = {"connection_id": connection, "model": "fixture-model", "prompt": "Fixture"}
        self.ledger("angelo_studio_generate", args, dict(asset), recorded_name="toonlab_studio_generate",
                    invocation_digest=digest("angelo_studio_generate", args))
        call, request = self.request({"assets": [{"source": "ASSET", "id": asset["id"], "label": "Mismatched"}]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, request)

    def test_old_document_failed_reverted_and_noncompleted_asset_receipts_are_refused(self):
        library, asset, connection = self.asset()
        for overrides in ({"document": repr(("other", 2))}, {"phase": "execution_started"},
                          {"approved": False}, {"automatic": True}):
            self.ledger("angelo_studio_generate", {"connection_id": connection, "model": "fixture-model", "prompt": "Fixture"},
                        dict(asset), **overrides)
        identity = self.ledger("angelo_studio_import_3d", {"asset_id": asset["id"]}, {"success": False})
        call, args = self.request({"assets": [{"source": "ASSET", "id": asset["id"], "label": "No real successful approval"}]})
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)
        record = self.controller.state["results"][identity]  # Records are replaced, never changed in place.
        self.controller.state["results"][identity] = dict(record, result=dict(record["result"], success=True))
        self.controller.state["messages"][-1]["reverted"] = True
        with self.assertRaises(ValueError):
            precondition(self.controller, call.name, args)


if __name__ == "__main__":
    unittest.main()
