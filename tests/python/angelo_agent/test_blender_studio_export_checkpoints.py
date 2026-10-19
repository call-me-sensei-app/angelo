# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated large Studio export -> native snapshot restore -> safety undo.

Run serially after performance measurements, with --background --factory-startup.
No inference, login, real user assets, provider workers or paid calls are used.
"""

import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from unittest.mock import Mock

import addon_utils
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_suite_paths  # noqa: E402


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        while data := source.read(1024 * 1024):
            result.update(data)
    return result.hexdigest()


def main():
    with native_suite_paths.temporary_directory("angelo-native-export-") as directory:   # Its journals pass MAX_PATH.
        root = Path(directory).resolve()
        os.environ["ANGELO_STATE_DIR"] = str(root / "state")
        os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
        os.environ["ANGELO_DISABLE_DEFAULT_CHAT"] = "1"
        from angelo_agent import ui
        from angelo_agent.checkpoint_policy import BEFORE_FIRST_EDIT, CheckpointPolicyStore
        from angelo_agent.files import MAX_FILE
        from angelo_agent.studio_agent import StudioAgent
        from angelo_agent.studio_state import StudioLibrary
        ui._authentication.start = lambda *args, **kwargs: None
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
        original = root / "original-project.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(original))
        original_hash = digest(original)
        controller = ui.get_controller()
        library = StudioLibrary(root / "library")
        asset = library.add("3D", "Factory-startup local fixture", "No provider", status="ready")
        source = library.result_directory(asset["id"]) / "model.glb"
        payload_bytes = MAX_FILE + 1024 * 1024
        metadata = json.dumps({"asset": {"version": "2.0"}, "buffers": [{"byteLength": payload_bytes}],
                               "scene": 0, "scenes": [{"nodes": []}]}).encode()
        metadata += b" " * (-len(metadata) % 4)
        total = 12 + 8 + len(metadata) + 8 + payload_bytes
        with source.open("wb") as output:
            output.write(struct.pack("<4sII", b"glTF", 2, total))
            output.write(struct.pack("<II", len(metadata), 0x4E4F534A) + metadata)
            output.write(struct.pack("<II", payload_bytes, 0x004E4942))
            output.truncate(total)
        asset["files"] = [source.name]
        library.update(asset)
        source_hash = digest(source)
        jobs, binding = Mock(), Mock()
        service = StudioAgent(library, jobs, lambda: [], binding, controller, Mock(), Mock())

        class Executor:
            def precondition(self, name, args):
                return service.precondition(name, args)

            def execute(self, name, args):
                return service.execute(name, args)

        CheckpointPolicyStore(controller.store.directory).save(
            {"capture_mode": BEFORE_FIRST_EDIT}, user_confirmed=True)
        controller._initial_request_checkpoint()
        assert controller.request_checkpoint is None
        controller.busy = True
        args = {"asset_id": asset["id"], "path": "exports/model.glb"}
        answers, executor = [], Executor()
        controller.request_tool("native-unbilled-export", "angelo_studio_export", args, answers.append)
        controller.pump_tools(executor, controller.document)
        call = next(iter(controller.pending.values()))
        assert call.approval and controller.request_checkpoint is None
        controller.execute_tool(call, executor, controller.document)
        controller.busy = False
        assert answers and answers[0].get("success"), answers
        checkpoint = controller.request_checkpoint
        target = controller.files.root / args["path"]
        assert target.stat().st_size > MAX_FILE and digest(target) == source_hash
        assert answers[0]["journaled"] and answers[0]["file_changes"]["written"] == [args["path"]]
        assert not list(target.parent.glob("*.tmp"))
        assert 'FINISHED' in bpy.ops.angelo.restore_checkpoint(checkpoint_id=checkpoint)
        assert not target.exists() and digest(original) == original_hash
        controller.pump_events()
        safety = next(message["restore_checkpoint"] for message in reversed(controller.state["messages"])
                      if message.get("restore_checkpoint"))
        record = controller.checkpoints.load(safety)
        assert record["purpose"] == "safety" and record["protected"]
        assert record["files"][0]["kind"] == "studio_export_safety"
        assert 'FINISHED' in bpy.ops.angelo.restore_checkpoint(checkpoint_id=safety)
        assert target.stat().st_size == total and digest(target) == source_hash
        assert digest(source) == source_hash and digest(original) == original_hash
        binding.assert_not_called()
        jobs.start.assert_not_called()
        addon_utils.disable("angelo_agent", default_set=False)
        print("ANGELO_STUDIO_EXPORT_CHECKPOINT_OK deferred_capture=true export_restored=true "
              "safety_undo=true original_unchanged=true unbilled=true bytes=" + str(total), flush=True)


main()
