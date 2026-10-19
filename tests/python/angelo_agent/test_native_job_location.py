# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A job's start receipt says where its output lands, without a local path.

Recorded one-shots often ended with the agent telling the person it did not
know where the render was saved or whether its name was final. The receipt
now names the folder its workspace-relative ``output`` belongs to and the name
it falls back to when that one is taken, for every kind of output.
"""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_host  # noqa: E402
from angelo_agent.job_output_names import fallback_name  # noqa: E402
from angelo_agent.job_spec import validate_job  # noqa: E402

PROJECT = Path("/Users/someone/Projects/bridge")  # A local path: never part of what the model reads.
STATE = Path("/Users/someone/Library/Application Support/Angelo/angelo-agent/workspace")


class Manager:
    queue = ()

    def __init__(self, status="running"):
        self.status = status

    def submit(self, chat, call, request, runner):
        return {"id": "j3", "status": self.status, "reason": "", "receipt": {}}

    def locks_interface(self):
        return False


class StartReceiptLocationTests(unittest.TestCase):
    def start(self, arguments, *, root=PROJECT, publishes="file", output_name=None, status="running"):
        ui = types.ModuleType("angelo_agent.job_ui")
        manager = Manager(status)
        ui.manager = lambda: manager
        request = validate_job("angelo_job_start", arguments)
        runner = types.SimpleNamespace(output_name=output_name or request.get("output"), publishes=publishes)
        controller = types.SimpleNamespace(state={"id": "chat"}, files=types.SimpleNamespace(root=root),
                                           workspace=STATE, executing_call=None)
        with patch.dict(sys.modules, {"angelo_agent.job_ui": ui}), \
                patch.object(job_host, "_admit", return_value=(request, runner)):
            return job_host.execute(controller, object(), "angelo_job_start", arguments)

    def test_a_saved_project_names_the_blend_files_folder_and_the_fallback(self):
        result = self.start({"kind": "RENDER_STILL", "output": "renders/bridge-final.png"})
        self.assertEqual(result["output"], "renders/bridge-final.png")
        self.assertEqual(result["workspace"], job_host.WORKSPACE_PROJECT)
        self.assertEqual(result["if_taken"], "renders/bridge-final-j3.png")
        self.assertNotIn(str(PROJECT), json.dumps(result))

    def test_an_unsaved_file_names_the_chats_own_folder_and_its_open_button(self):
        result = self.start({"kind": "RENDER_STILL", "output": "tree-final.png"}, root=STATE)
        self.assertEqual(result["workspace"], job_host.WORKSPACE_CHAT)
        self.assertIn("Open button", result["workspace"])
        self.assertNotIn(str(STATE), json.dumps(result))

    def test_every_publication_kind_states_its_own_fallback(self):
        cases = [({"kind": "RENDER_ANIMATION", "output": "renders/drive"}, "folder", None, "renders/drive-j3"),
                 ({"kind": "RENDER_STILL", "output": "renders/stereo.png"}, "folder", "renders/stereo",
                  "renders/stereo-j3"),
                 ({"kind": "EXPORT_USD", "output": "export/scene.usdc"}, "file_set", None,
                  "export/scene-j3/scene.usdc")]
        for arguments, publishes, name, fallback in cases:
            with self.subTest(kind=arguments["kind"], publishes=publishes):
                result = self.start(arguments, publishes=publishes, output_name=name)
                self.assertEqual(result["output"], name or arguments["output"])
                self.assertEqual(result["if_taken"], fallback)

    def test_queued_and_failed_starts_carry_the_same_location(self):
        for status in ("queued", "failed"):
            with self.subTest(status=status):
                result = self.start({"kind": "RENDER_STILL", "output": "a.png"}, status=status)
                self.assertEqual((result["workspace"], result["if_taken"]), (job_host.WORKSPACE_PROJECT, "a-j3.png"))

    def test_a_controller_without_a_workspace_still_gives_the_fallback(self):
        result = self.start({"kind": "RENDER_STILL", "output": "a.png"}, root=None)
        self.assertNotIn("workspace", result)
        self.assertEqual(result["if_taken"], "a-j3.png")


class CodexCorrectionPromptTests(unittest.TestCase):
    def test_a_plan_stopped_after_a_job_start_keeps_its_id_and_location(self):
        from angelo_agent.program_completion import continuation_receipt
        result = {"job": "j3", "status": "running", "kind": "RENDER_STILL", "note": "Runs in the background.",
                  "output": "renders/a.png", "workspace": job_host.WORKSPACE_PROJECT, "if_taken": "renders/a-j3.png"}
        receipt = continuation_receipt({"tool": "angelo_job_start", "arguments": {}}, result)
        self.assertEqual(receipt, {"tool": "angelo_job_start", "job": "j3", "status": "running",
                                   "output": "renders/a.png", "workspace": job_host.WORKSPACE_PROJECT,
                                   "if_taken": "renders/a-j3.png"})


class FallbackNameTests(unittest.TestCase):
    """``fallback_name`` is the disk-free statement of ``Staging._candidate``'s second name."""

    def test_it_matches_the_staging_candidates(self):
        from angelo_agent.job_outputs import Staging
        for relative, publishes in (("renders/a.png", "file"), ("renders/anim", "folder"), ("out/s.usd", "file_set"),
                                    ("deep/x/y.exr", "file"), ("cache", "folder")):
            with self.subTest(relative=relative, publishes=publishes):
                staging = Staging.__new__(Staging)
                staging.output, staging.identity = Path("/w") / relative, "j7"
                folder, dependencies = publishes == "folder", publishes == "file_set"
                second = staging._candidate(folder, dependencies)[1]
                expected = str(Path(relative).parent / second) if publishes != "file_set" else \
                    str(Path(relative).parent / second / Path(relative).name)
                self.assertEqual(fallback_name(relative, "j7", publishes), expected.removeprefix("./"))


if __name__ == "__main__":
    unittest.main()
