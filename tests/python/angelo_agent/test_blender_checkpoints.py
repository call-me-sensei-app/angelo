# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated native save/restore test. No model, login or microphone is started."""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def main():
    with tempfile.TemporaryDirectory(prefix="angelo-checkpoints-") as directory:
        root = Path(directory)
        os.environ["ANGELO_STATE_DIR"] = str(root / "state")
        os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
        from angelo_agent import ui
        from angelo_agent.files import FileWorkspace
        from angelo_agent.blender_tools import BlenderExecutor
        ui._authentication.start = lambda *args, **kwargs: None
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
        try:
            original = root / "original-project.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(original))
            original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            controller = ui.get_controller()
            controller.files = FileWorkspace(root, excluded=[root / "state"])
            notes = root / "notes.md"
            notes.write_text("original notes")
            checkpoint = ui.capture_checkpoint(controller)
            assert bpy.data.filepath == str(original), "Saving a request checkpoint must not rename the user's project"
            controller.state["messages"].append({"role": "user", "text": "Native checkpoint fixture", "checkpoint": checkpoint["id"]})
            controller.files.execute("angelo_write_file", {"path": "notes.md", "content": "changed notes"}, controller.checkpoints, checkpoint["id"])
            result = BlenderExecutor(ui.main_window()).execute("angelo_execute_python", {
                "description": "Create a native recovery test sphere", "code": "bpy.ops.mesh.primitive_uv_sphere_add()\nbpy.context.active_object.name = 'CheckpointSphere'\nresult = {'object': bpy.context.active_object.name}",
            })
            assert result["success"] and "CheckpointSphere" in bpy.data.objects
            assert len(result["changes"]["added"]) == 1 and not result["changes"]["modified"], "Dependency graph refreshes must not fabricate changes to unchanged objects"
            later = ui.capture_checkpoint(controller)
            controller.files.execute("angelo_write_file", {"path": "later.md", "content": "later request"}, controller.checkpoints, later["id"])
            restore = bpy.ops.angelo.restore_checkpoint(checkpoint_id=checkpoint["id"])
            assert 'FINISHED' in restore
            assert "CheckpointSphere" not in bpy.data.objects
            assert notes.read_text() == "original notes" and not (root / "later.md").exists()
            assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash, "Restore must not overwrite the original project"
            assert bpy.data.filepath != str(original), "Restore must open a working recovery copy"
            assert not controller.document_invalid and controller.document == ui.document_key()
            assert controller.state["messages"][0]["text"] == "Native checkpoint fixture"
            # Restoring must not consume the restore point. The background snapshot
            # worker may already have replaced its full copy with a verified
            # deduplicated manifest, so check availability, not scene.blend.
            assert controller.checkpoints.availability(checkpoint["id"])["available"]
        finally:
            # Disable while the temporary profile still exists, including failed
            # captures/restores; unregister saves the current controller session.
            addon_utils.disable("angelo_agent", default_set=False)
        print("ANGELO_CHECKPOINT_TEST_OK scene_restored=true files_restored=true original_unchanged=true history_kept=true", flush=True)


main()
