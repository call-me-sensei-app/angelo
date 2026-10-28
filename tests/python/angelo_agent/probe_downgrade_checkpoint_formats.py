# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual downgrade probe: does a RELEASED Angelo build still list and Restore a checkpoint a newer build wrote?

Run it with the released app and its own bundled add-on (never this tree's), in a disposable copy of the app, with an
isolated home and state folder; the probe makes its own temporary state folder::

    H=$(mktemp -d); HOME=$H CFFIXED_USER_HOME=$H nice -n 15 <copy>/Angelo.app/Contents/MacOS/Angelo --background \
        --factory-startup --disable-autoexec --python-exit-code 1 --python probe_downgrade_checkpoint_formats.py -- MODE [other]

MODE: ``none`` (the control), ``sidecar`` (a ``scenes.json`` scene map beside ``checkpoint.json``), ``record`` (the
same map as a ``scenes`` key inside ``checkpoint.json``) or ``folder`` (the same map in a profile folder outside
``checkpoints``: ``checkpoint-scenes/<session>/<checkpoint>.json``). The probe captures a checkpoint through the build's own path
(``ui.capture_checkpoint``), applies MODE, then reports what that build makes of it: the profile inventory's issues,
whether the checkpoint is listed and available, and a Restore after a later edit: of that checkpoint, or with
``other`` of an earlier checkpoint left as the build wrote it (the profile inventory gates every Restore). One line:
``DOWNGRADE-PROBE {json}``. Not collected by any suite.

Recorded 2026-10-25 with the released Angelo 1.0.5 (app 8af1c2a0a35, its add-on identical to the v1.0.5 tag), in a
copy of the app on a macOS test Mac, each mode in a fresh home and state folder:

* ``none`` and ``none other``: inventory issues [], listed, Restore FINISHED ("complete").
* ``folder`` and ``folder other`` (the layout Angelo keeps scene maps in, ``checkpoint_scene_map``): inventory
  issues [], listed, available, Restore FINISHED ("complete"), of the mapped checkpoint and of the earlier one.
* ``sidecar`` (and ``other``): inventory issue "Unrecognized files in snapshot storage; no pruning is permitted",
  not listed, Restore refused: "File restore is unavailable: Unrecognized files in snapshot storage; ...", for the
  earlier untouched checkpoint too.
* ``record`` (and ``other``): inventory issue "Invalid snapshot metadata", unavailable, Restore refused: "Invalid
  snapshot metadata" ("File restore is unavailable: Invalid snapshot metadata" for the earlier checkpoint).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
MODE, OTHER = (ARGS or ["none"])[0], "other" in ARGS[1:]
assert MODE in {"none", "sidecar", "record", "folder"}, MODE
report = {"mode": MODE, "restore_other": OTHER, "binary": bpy.app.binary_path, "version": bpy.app.version_string}

with tempfile.TemporaryDirectory(prefix="angelo-downgrade-probe-") as directory:
    root = Path(directory)
    os.environ["ANGELO_STATE_DIR"] = str(root / "state")
    os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
    from angelo_agent import ui   # The app's bundled add-on: this probe adds no source tree to sys.path.
    report["addon"] = ui.__file__
    ui._authentication.start = lambda *args, **kwargs: None   # No sign-in.
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    try:
        bpy.context.window_manager.windows[0].scene.name = "Atelier"
        bpy.data.scenes.new("Harbor")
        bpy.ops.wm.save_as_mainfile(filepath=str(root / "project.blend"))
        controller = ui.get_controller()
        earlier = ui.capture_checkpoint(controller)   # Left as this build wrote it.
        controller.state["messages"].append({"role": "user", "text": "Earlier", "checkpoint": earlier["id"],
                                             "checkpoint_scope": "before_request"})
        point = ui.capture_checkpoint(controller)
        controller.state["messages"].append({"role": "user", "text": "Downgrade probe", "checkpoint": point["id"],
                                             "checkpoint_scope": "before_request"})
        folder = controller.checkpoints.folder(point["id"])
        scenes = {"load": ui._document_serial, "names": [[s.session_uid, s.name_full] for s in bpy.data.scenes]}
        if MODE == "sidecar":
            sidecar = folder / "scenes.json"
            sidecar.write_text(json.dumps(scenes))
            os.chmod(sidecar, 0o600)
        elif MODE == "folder":
            outside = Path(controller.checkpoints.root) / "checkpoint-scenes" / controller.checkpoints.session_id
            outside.mkdir(parents=True, mode=0o700)
            for identity in (earlier["id"], point["id"]):   # As 1.0.6 keeps them (checkpoint_scene_map).
                (outside / (identity + ".json")).write_text(json.dumps(scenes))
                os.chmod(outside / (identity + ".json"), 0o600)
            report["outside"] = sorted(str(path.relative_to(controller.checkpoints.root)) for path in outside.iterdir())
        elif MODE == "record":
            record = folder / "checkpoint.json"
            record.write_text(json.dumps(dict(json.loads(record.read_text()), scenes=scenes)))
        report["files"] = sorted(path.name for path in folder.iterdir())
        inventory = controller.checkpoints.inspect()
        report["inventory_issues"] = [issue.get("error") for issue in inventory["issues"]]
        report["listed"] = any(item["id"] == point["id"] for item in inventory["checkpoints"])
        report["availability"] = controller.checkpoints.availability(point["id"])
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.active_object.name = "AfterCheckpoint"
        try:
            target = earlier["id"] if OTHER else point["id"]
            report["restore"] = sorted(bpy.ops.angelo.restore_checkpoint(checkpoint_id=target))
        except RuntimeError as error:   # A background operator's ERROR report raises.
            report["restore"], report["restore_error"] = ["RAISED"], str(error)
        report["edit_after_checkpoint_still_there"] = "AfterCheckpoint" in bpy.data.objects
        notice = next((m for m in reversed(controller.state["messages"]) if m.get("restore_checkpoint")), None)
        report["restore_state"] = notice and notice.get("restore_state")
    finally:
        addon_utils.disable("angelo_agent", default_set=False)
print("DOWNGRADE-PROBE " + json.dumps(report, default=str), flush=True)
