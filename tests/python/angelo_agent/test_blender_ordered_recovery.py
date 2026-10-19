# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Factory-only real scene saves plus ordered file/large-export round trips."""

import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts/addons_core"))
sys.path.insert(1, str(Path(__file__).resolve().parent))
import native_suite_paths  # noqa: E402  (standard library only)
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.checkpoint_policy import checkpoint_ref
from angelo_agent.files import FileWorkspace, MAX_FILE
from angelo_agent.studio_agent import StudioAgent
from angelo_agent.studio_state import StudioLibrary

checks = []


def check(name, condition):
    checks.append({"name": name, "passed": bool(condition)})
    assert condition, name


with tempfile.TemporaryDirectory(prefix="angelo-native-ordered-", dir=native_suite_paths.short_temp_root()) as directory:
    root = Path(directory).resolve()
    project = root / "project"
    project.mkdir()
    os.environ["ANGELO_STATE_DIR"] = str(root / "state")
    os.environ["ANGELO_CONNECTIONS_DIR"] = str(root / "connections")
    a = CheckpointStore(root / "state", str(uuid.uuid4()))
    b = CheckpointStore(root / "state", str(uuid.uuid4()))
    files = FileWorkspace(project)
    original = project / "original.blend"
    bpy.context.preferences.filepaths.file_preview_type = "NONE"
    bpy.ops.wm.save_as_mainfile(filepath=str(original))
    original_digest = hashlib.sha256(original.read_bytes()).hexdigest()

    def snapshot(store, purpose="request"):
        record, path = store.reserve(str(original), purpose=purpose)
        result = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, relative_remap=True, check_existing=False)
        check("native_snapshot_save_finished", "FINISHED" in result and path.is_file())
        return store.complete(record["id"])

    def write(store, record, text, path="notes.md"):
        return files.execute("angelo_write_file", {"path": path, "content": text}, store, record["id"])

    notes = project / "notes.md"
    notes.write_text("original")
    ca = snapshot(a)
    write(a, ca, "first A")
    bpy.data.objects["Cube"].location.x = 3
    cb = snapshot(b)
    write(a, ca, "second A after B")
    bpy.data.objects["Cube"].location.x = 9
    versions = a.load(ca["id"])["files"]
    check("straddled_old_request_has_two_before_images", len(versions) == 2)
    check("intermediate_before_image_exact", (a.folder(ca["id"]) / versions[1]["backup"]).read_text() == "first A")
    refs = [checkpoint_ref(b.session_id, cb["id"])]
    plan = b.prune_plan({"max_count": 1}, protected_refs=refs)
    check("retention_keeps_old_owner_late_version", ca["id"] not in [item["id"] for item in plan["candidates"]])
    _, recovery = b.recovery_copy(cb["id"])
    bpy.ops.wm.open_mainfile(filepath=str(recovery), load_ui=False, use_scripts=False)
    check("actual_native_scene_recovers_intermediate_cube", bpy.data.objects["Cube"].location.x == 3)
    restored = b.restore_since(cb["id"])
    check("ordered_file_restore_count", restored == 1 and restored.changed == 1)
    check("actual_file_recovers_intermediate_A_bytes", notes.read_text() == "first A")
    again = b.restore_since(cb["id"])  # The same bytes again: a restore that changes no file says so.
    check("no_op_file_restore_changes_nothing", again == 1 and again.changed == 0 and notes.read_text() == "first A")
    check("original_project_unchanged_after_recovery", hashlib.sha256(original.read_bytes()).hexdigest() == original_digest)
    check("working_copy_not_original", bpy.data.filepath != str(original))

    target = project / "model.glb"
    target.write_bytes(b"original small fixture model")
    before_delete = snapshot(a)
    files.execute("angelo_delete_file", {"path": "model.glb"}, a, before_delete["id"])
    before_export = snapshot(b)
    library = StudioLibrary(root / "library")
    asset = library.add("3D", "Native local export fixture", "No provider", status="ready")
    source = library.result_directory(asset["id"]) / "model.glb"
    with source.open("wb") as output:
        output.truncate(MAX_FILE + 2 * 1024 * 1024)
    asset["files"] = [source.name]
    library.update(asset)
    controller = SimpleNamespace(checkpoints=b, request_checkpoint=before_export["id"], files=files)
    service = StudioAgent(library, Mock(), lambda: [], Mock(), controller, Mock(), Mock())
    result = service.execute("angelo_studio_export", {"asset_id": asset["id"], "path": "model.glb"})
    check("native_large_export_succeeds", result["success"] and target.stat().st_size == MAX_FILE + 2 * 1024 * 1024)
    check("large_export_does_not_poison_old_journal", a.inspect()["issues"] == [])
    file_plan = a.restore_plan(before_delete["id"])
    check("old_regular_backup_gets_current_export_evidence", file_plan[0][2]["current_kind"] == "studio_export" and file_plan[0][2].get("kind", "file_tool") == "file_tool")
    safety = snapshot(a, purpose="safety")
    for _, _, entry in file_plan:
        a.journal(safety["id"], Path(entry["root"]), entry["path"], restore_entry=entry)
    check("large_export_safety_backup_is_protected", a.load(safety["id"])["protected"] and a.load(safety["id"])["files"][0]["kind"] == "studio_export_safety")
    a.restore_since(before_delete["id"], file_plan)
    check("small_original_file_restored", target.read_bytes() == b"original small fixture model")
    a.restore_since(safety["id"])
    check("undo_restore_recovers_full_large_export", hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest())
    check("all_ordered_journals_remain_valid", a.inspect()["issues"] == [])
    check("original_native_project_still_unchanged", hashlib.sha256(original.read_bytes()).hexdigest() == original_digest)

report = {"binary": bpy.app.binary_path, "build_hash": bpy.app.build_hash.decode(),
          "opensubdiv": bpy.app.build_options.opensubdiv, "checks": checks,
          "passed": sum(item["passed"] for item in checks), "total": len(checks)}
output = os.environ.get("ANGELO_ORDERED_RECOVERY_REPORT")
if output:
    Path(output).write_text(json.dumps(report, indent=2))
print("ANGELO_ORDERED_RECOVERY_OK " + json.dumps(report), flush=True)
