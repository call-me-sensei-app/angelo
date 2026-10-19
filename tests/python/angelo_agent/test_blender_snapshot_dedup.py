# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native deduplicated snapshots: real captures, ingestion and exact restores.

Run with the exact Angelo binary and fresh isolated directories (or through
run_native_suites.py, which provides them)::

  QA=$(mktemp -d) BLENDER_USER_CONFIG=$QA/config ANGELO_QA_DIR=$QA \\
    ANGELO_CONNECTIONS_DIR=$QA/accounts ANGELO_STATE_DIR=$QA/state \\
    "$ANGELO" --background --factory-startup --disable-autoexec \\
    --python-exit-code 1 --python tests/python/angelo_agent/test_blender_snapshot_dedup.py

Proves in the compiled binary: product ``ui.capture_checkpoint`` captures after
real edits (transform, material, modifier, vertex edit, added and deleted
objects) are adopted by the background snapshot worker, both as pre-existing
full copies (start-up migration) and straight after capture; each recorded
whole-file hash equals the hash of the captured full copy; every snapshot
restores through the product restore operator to exactly its captured scene
(object set, world matrices, mesh vertex data, materials, modifiers); and the
store holds far fewer bytes than the full copies. Only a factory-startup
fixture inside ANGELO_QA_DIR is used; no user project, provider or account.
Inspect snapshot-dedup.json: a zero exit status alone is not proof.
"""

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import traceback

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-snapshot-dedup"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import snapshot_service, ui  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore  # noqa: E402

report = {"checks": {}, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "paid_requests": 0, "user_projects_opened": 0, "snapshots": []}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def scene_state():
    """Exact, comparable state of every object in the current document."""
    state = {}
    for obj in bpy.data.objects:
        entry = {"type": obj.type, "matrix": [round(value, 5) for row in obj.matrix_world for value in row],
                 "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
                 "modifiers": [modifier.type for modifier in obj.modifiers]}
        if obj.type == "MESH":
            coords = [0.0] * (len(obj.data.vertices) * 3)
            obj.data.vertices.foreach_get("co", coords)
            entry["vertices"] = len(obj.data.vertices)
            entry["mesh"] = hashlib.sha256(struct.pack("%df" % len(coords), *coords)).hexdigest()
        state[obj.name] = entry
    return state


def file_hash(path):
    digest = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as handle:
        while data := handle.read(8 * 1024 * 1024):
            digest.update(data)
    return digest.hexdigest()


def wait_until(predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def build_document():
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=220, y_subdivisions=220, size=4, location=(6, 0, 0))
    bpy.context.active_object.name = "DenseGrid"
    for index in range(40):
        bpy.ops.mesh.primitive_cube_add(size=0.3, location=(index % 8, 4 + index // 8, 0))
        bpy.context.active_object.name = "Prop%02d" % index


EDITS = (
    ("base", lambda: None),
    ("move_one_object", lambda: setattr(bpy.data.objects["Prop03"].location, "x", 9.5)),
    ("add_material", lambda: bpy.data.objects["Cube"].data.materials.append(bpy.data.materials.new("SnapshotRed"))),
    ("add_modifier", lambda: bpy.data.objects["DenseGrid"].modifiers.new("Bevel", "BEVEL")),
    ("edit_vertices", lambda: edit_vertices(bpy.data.objects["DenseGrid"].data)),
    ("add_object", lambda: bpy.ops.mesh.primitive_uv_sphere_add(location=(0, -4, 0))),
    ("delete_object", lambda: bpy.data.objects.remove(bpy.data.objects["Prop07"])),
)


def edit_vertices(mesh):
    coords = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", coords)
    for index in range(2, len(coords) // 4, 3):
        coords[index] += 0.25
    mesh.vertices.foreach_set("co", coords)
    mesh.update()


def main():
    ui._authentication.start = lambda *args, **kwargs: None
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    check("worker_started_with_the_add_on", snapshot_service.running(ui.state_directory()))
    controller = ui.get_controller()
    # Phase 1: capture full copies with the worker stopped, hash them exactly,
    # then let start-up recovery migrate them (the pre-deduplication path).
    snapshot_service.stop()
    build_document()
    snapshots = []
    for name, edit in EDITS:
        edit()
        bpy.context.view_layer.update()
        record = ui.capture_checkpoint(controller)
        scene = controller.checkpoints.folder(record["id"]) / "scene.blend"
        snapshots.append({"name": name, "id": record["id"], "bytes": record["scene_bytes"], "hash": file_hash(scene),
                          "capture_ms": record.get("capture_ms"), "state": scene_state()})
    # Fresh captures are private v4 records, which state their storage ("full")
    # instead of omitting it as version-3 records did.
    records = [controller.checkpoints.load(item["id"]) for item in snapshots]
    report["records_before_migration"] = [{key: record.get(key) for key in ("version", "storage", "state")}
                                          for record in records]
    check("full_copies_before_migration", all(
        record.get("storage", "full") == "full" and (controller.checkpoints.folder(item["id"]) / "scene.blend").is_file()
        for record, item in zip(records, snapshots)))
    started = time.monotonic()
    snapshot_service.start(ui.state_directory())
    check("startup_recovery_adopts_every_full_copy", wait_until(lambda: all(
        controller.checkpoints.load(item["id"]).get("storage") == "store" for item in snapshots)))
    report["migration_seconds"] = round(time.monotonic() - started, 3)
    for item in snapshots:
        record = controller.checkpoints.load(item["id"])
        check(item["name"] + "_recorded_hash_matches_captured_file", record["scene_hash"] == item["hash"])
        check(item["name"] + "_full_copy_deleted", not (controller.checkpoints.folder(item["id"]) / "scene.blend").exists())
    # Phase 2: the live product path, ingestion straight after capture.
    bpy.data.objects["Prop11"].location.z = 2.5
    # Evaluate as phase 1 does: scene_state reads world matrices, and the native
    # capture writes the file without evaluating the live depsgraph.
    bpy.context.view_layer.update()
    live = ui.capture_checkpoint(controller)
    snapshots.append({"name": "live_capture", "id": live["id"], "bytes": live["scene_bytes"],
                      "capture_ms": live.get("capture_ms"), "state": scene_state()})
    check("live_capture_adopted", wait_until(lambda: controller.checkpoints.load(live["id"]).get("storage") == "store"))
    store = SnapshotStore(controller.checkpoints.root)
    usage = store.usage()
    full_bytes = sum(item["bytes"] for item in snapshots)
    report["store"] = dict(usage, full_copy_bytes=full_bytes)
    check("store_is_much_smaller_than_full_copies", usage["live_bytes"] < full_bytes / 3)
    # Phase 3: exact restores through the product operator, oldest first.
    for item in snapshots:
        record = controller.checkpoints.load(item["id"])
        started = time.monotonic()
        _, copy = controller.checkpoints.recovery_copy(item["id"])
        item["reassemble_ms"] = round((time.monotonic() - started) * 1000, 1)
        check(item["name"] + "_reassembled_bytes_match_hash", file_hash(copy) == record["scene_hash"]
              and copy.stat().st_size == record["scene_bytes"])
        copy.unlink()
        started = time.monotonic()
        result = bpy.ops.angelo.restore_checkpoint(checkpoint_id=item["id"])
        item["restore_operator_ms"] = round((time.monotonic() - started) * 1000, 1)
        check(item["name"] + "_restore_finished", "FINISHED" in result)
        restored = scene_state()
        if restored != item["state"]:
            report.setdefault("restore_differences", {})[item["name"]] = {
                name: {"captured": item["state"].get(name), "restored": restored.get(name)}
                for name in sorted(set(restored) | set(item["state"])) if restored.get(name) != item["state"].get(name)}
        check(item["name"] + "_scene_restored_exactly", restored == item["state"])
        controller = ui.get_controller()
    check("all_points_still_available", all(controller.checkpoints.availability(item["id"])["available"]
                                             for item in snapshots))
    check("history_has_no_issues", controller.checkpoints.inspect()["issues"] == [])
    report["snapshots"] = [{key: value for key, value in item.items() if key != "state"} for item in snapshots]
    addon_utils.disable("angelo_agent", default_set=False)
    check("worker_stopped_with_the_add_on", not snapshot_service.running(ui.state_directory()))


try:
    main()
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    (OUT / "snapshot-dedup.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SNAPSHOT_DEDUP " + json.dumps({"success": report["success"], "checks": len(report["checks"]),
                                             "store": report.get("store")}), flush=True)
if not report["success"]:
    sys.exit(1)
