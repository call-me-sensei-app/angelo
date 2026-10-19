# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checkpoint fixture teardown under injected failures; no native fidelity claim."""

import contextlib
import io
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest.mock import patch


class CheckpointFixtureCleanupTests(unittest.TestCase):
    def run_failure(self, phase):
        failure = RuntimeError(phase + " failed")
        events, observations = [], []
        data = types.SimpleNamespace(filepath="", objects={})
        controller = types.SimpleNamespace(state={"messages": []}, checkpoints=object())

        class Workspace:
            def __init__(self, root, **kwargs):
                self.root = root

            def execute(self, name, arguments, *args):
                (self.root / arguments["path"]).write_text(arguments["content"])

        class Executor:
            def __init__(self, window):
                pass

            def execute(self, *args):
                data.objects["CheckpointSphere"] = object()
                return {"success": True, "changes": {"added": ["CheckpointSphere"], "modified": []}}

        def save(filepath):
            Path(filepath).write_bytes(b"original project")
            data.filepath = filepath

        def capture(target):
            if phase == "capture":
                raise failure
            return {"id": "fixture-point"}

        def restore(**kwargs):
            raise failure

        def disable(*args, **kwargs):
            events.append("disable")
            root = Path(data.filepath).parent
            # Production unregister saves the controller here. Removing this
            # profile first would mask the actual capture/restore error.
            self.assertTrue(root.is_dir())
            observations.append({"notes": (root / "notes.md").read_text(),
                                 "sphere": "CheckpointSphere" in data.objects,
                                 "later": (root / "later.md").exists()})

        ui = types.SimpleNamespace(_authentication=types.SimpleNamespace(start=None),
                                   get_controller=lambda: controller,
                                   capture_checkpoint=capture, main_window=lambda: object())
        modules = {
            "addon_utils": types.SimpleNamespace(enable=lambda *args, **kwargs: events.append("enable"),
                                                 disable=disable),
            "bpy": types.SimpleNamespace(app=types.SimpleNamespace(background=True), data=data,
                                         ops=types.SimpleNamespace(wm=types.SimpleNamespace(save_as_mainfile=save),
                                                                   angelo=types.SimpleNamespace(restore_checkpoint=restore))),
            "angelo_agent": types.SimpleNamespace(ui=ui),
            "angelo_agent.files": types.SimpleNamespace(FileWorkspace=Workspace),
            "angelo_agent.blender_tools": types.SimpleNamespace(BlenderExecutor=Executor),
        }
        fixture = Path(__file__).with_name("test_blender_checkpoints.py")
        output = io.StringIO()
        with patch.dict(sys.modules, modules), patch.object(sys, "argv", [str(fixture), "--factory-startup"]), \
                patch.object(sys, "path", list(sys.path)), patch.dict("os.environ"), contextlib.redirect_stdout(output):
            with self.assertRaises(RuntimeError) as caught:
                runpy.run_path(str(fixture))
        self.assertIs(caught.exception, failure)
        self.assertEqual(events, ["enable", "disable"])
        self.assertNotIn("ANGELO_CHECKPOINT_TEST_OK", output.getvalue())
        self.assertFalse(Path(data.filepath).parent.exists())
        return observations[0]

    def test_capture_failure_disables_before_temporary_profile_removal(self):
        self.assertEqual(self.run_failure("capture"), {"notes": "original notes", "sphere": False, "later": False})

    def test_restore_failure_preserves_current_edits_and_original_error(self):
        self.assertEqual(self.run_failure("restore"), {"notes": "changed notes", "sphere": True, "later": True})


if __name__ == "__main__":
    unittest.main()
