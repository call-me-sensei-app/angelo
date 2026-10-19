# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.approvals import approval_message
from angelo_agent.chat_settings import codex_turn_options, normalize_models
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.controller import Controller
from angelo_agent.files import FileWorkspace


class FilesAndCheckpointsTests(unittest.TestCase):
    def test_file_tools_refuse_hidden_paths_symlink_escapes_and_private_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            outside = Path(directory) / "outside.txt"
            outside.write_text("private")
            (root / "linked.txt").symlink_to(outside)
            private = root / "app-state"
            private.mkdir()
            (private / "auth.json").write_text("credential")
            files = FileWorkspace(root, excluded=[private])
            for path in ("../outside.txt", str(outside), ".env", "linked.txt", "app-state/auth.json"):
                with self.assertRaises(ValueError):
                    files.path(path)
            self.assertEqual(files.execute("angelo_list_files", {"path": "."})["entries"], [])

    def test_journal_restores_before_multiple_requests_and_other_chats(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Controller(directory)
            other = Controller(directory)
            workspace = Path(directory) / "project"
            workspace.mkdir()
            target = workspace / "notes.md"
            target.write_text("original")
            files = FileWorkspace(workspace)
            a, _ = first.checkpoints.reserve()
            files.execute("angelo_write_file", {"path": "notes.md", "content": "request A"}, first.checkpoints, a["id"])
            b, _ = other.checkpoints.reserve()
            files.execute("angelo_write_file", {"path": "notes.md", "content": "request B"}, other.checkpoints, b["id"])
            files.execute("angelo_write_file", {"path": "new.md", "content": "new file"}, other.checkpoints, b["id"])
            self.assertEqual(first.checkpoints.restore_since(a["id"]), 2)
            self.assertEqual(target.read_text(), "original")
            self.assertFalse((workspace / "new.md").exists())
            first.close()
            other.close()

    def test_a_second_write_keeps_the_first_pre_request_file_version(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            files = FileWorkspace(controller.workspace)
            (files.root / "data.txt").write_text("original")
            checkpoint, _ = controller.checkpoints.reserve()
            for text in ("one", "two"):
                files.execute("angelo_write_file", {"path": "data.txt", "content": text}, controller.checkpoints, checkpoint["id"])
            self.assertEqual(len(controller.checkpoints.load(checkpoint["id"])["files"]), 1)
            controller.checkpoints.restore_since(checkpoint["id"])
            self.assertEqual((files.root / "data.txt").read_text(), "original")
            controller.close()

    def test_missing_file_has_a_real_precondition_not_an_unchecked_none(self):
        with tempfile.TemporaryDirectory() as directory:
            files = FileWorkspace(directory)
            args = {"path": "new.txt", "content": "text"}
            before = files.precondition("angelo_write_file", args)
            (Path(directory) / "new.txt").write_text("someone else's change")
            self.assertNotEqual(before, files.precondition("angelo_write_file", args))

    def test_human_approval_is_not_a_json_payload(self):
        message = approval_message("angelo_set_transform", {"object": "AngeloLiveDemo", "location": [0, 0, 2]})
        self.assertEqual(message["title"], "Move AngeloLiveDemo")
        self.assertIn("X 0, Y 0, Z 2", message["body"])
        self.assertNotIn("{", message["body"])
        self.assertNotIn("angelo_set_transform", message["body"])

    def test_model_updates_are_next_turn_and_provider_default_really_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.models = normalize_models([
                {"model": "model-a", "isDefault": True, "supportedReasoningEfforts": [{"reasoningEffort": "low"}]},
                {"model": "model-b", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
            ])
            controller.set_options(model="model-a", effort="low")
            in_flight = dict(controller.state["options"])
            controller.busy = True
            controller.set_options(model="model-b", effort="high")
            self.assertEqual(codex_turn_options(controller, in_flight)["model"], "model-a")
            self.assertEqual(codex_turn_options(controller)["model"], "model-b")
            controller.set_options(model="")
            controller.state["model"] = "model-b"
            self.assertEqual(codex_turn_options(controller)["model"], "model-a")
            controller.close()

    def test_checkpoint_failure_does_not_start_or_consume_the_request(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.checkpoint_factory = Mock(side_effect=RuntimeError("snapshot failed"))
            with self.assertRaises(RuntimeError):
                controller.send("Create a sphere")
            self.assertFalse(controller.busy)
            self.assertEqual(controller.state["messages"], [])
            self.assertIsNone(controller.provider)
            controller.close()


if __name__ == "__main__":
    unittest.main()
